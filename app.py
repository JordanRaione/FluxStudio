import streamlit as st
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.path import Path
import textwrap
import re
import json
import base64
import io
import string
from pypdf import PdfReader, PdfWriter
from PIL import Image, PngImagePlugin
import pytesseract
from pdf2image import convert_from_bytes

# Configurações iniciais da página web
st.set_page_config(
    page_title="Gerador de Fluxogramas Operacionais",
    page_icon="📋",
    layout="wide"
)

matplotlib.rcParams['font.family'] = 'sans-serif'
matplotlib.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans']
matplotlib.rcParams['pdf.fonttype'] = 42
matplotlib.rcParams['ps.fonttype'] = 42

# ==============================================================================
# FUNÇÕES DE METADADOS E OCR
# ==============================================================================

def empacotar_metadados(cabecalho, texto_etapas):
    dados = {"cabecalho": cabecalho, "etapas": texto_etapas}
    json_str = json.dumps(dados, ensure_ascii=False)
    b64_str = base64.b64encode(json_str.encode('utf-8')).decode('ascii')
    return f"FLUXO_DATA:{b64_str}:END_FLUXO"

def desempacotar_metadados(texto_bruto):
    if not texto_bruto:
        return None, None
    match = re.search(r"FLUXO_DATA:([A-Za-z0-9+/=\s]+):END_FLUXO", str(texto_bruto))
    if match:
        try:
            b64_limpo = re.sub(r"\s+", "", match.group(1))
            json_str = base64.b64decode(b64_limpo.encode('ascii')).decode('utf-8')
            dados = json.loads(json_str)
            return dados.get("cabecalho", ""), dados.get("etapas", "")
        except Exception:
            pass
    return None, None

def reconstruir_fluxo_via_ocr(img_pil):
    largura_img, altura_img = img_pil.size
    try:
        dados_ocr = pytesseract.image_to_data(img_pil, lang='por', output_type=pytesseract.Output.DICT)
    except Exception:
        return None, None

    palavras = []
    n_boxes = len(dados_ocr['text'])
    for i in range(n_boxes):
        txt = dados_ocr['text'][i].strip()
        conf = int(dados_ocr['conf'][i])
        if txt and conf > 25:
            x = dados_ocr['left'][i] + dados_ocr['width'][i] / 2.0
            y = dados_ocr['top'][i] + dados_ocr['height'][i] / 2.0
            palavras.append({'x': x, 'y': y, 'txt': txt, 'bloco': dados_ocr['block_num'][i]})

    if not palavras:
        return None, None

    blocos = {}
    for p in palavras:
        b = p['bloco']
        if b not in blocos:
            blocos[b] = {'xs': [], 'ys': [], 'txts': []}
        blocos[b]['xs'].append(p['x'])
        blocos[b]['ys'].append(p['y'])
        blocos[b]['txts'].append(p['txt'])

    caixas_lidas = []
    for b, info in blocos.items():
        texto_bloco = " ".join(info['txts']).strip()
        texto_bloco = re.sub(r'^[|\[\]—\-_]+|[|\[\]—\-_]+$', '', texto_bloco).strip()
        if not texto_bloco:
            continue
        x_med = sum(info['xs']) / len(info['xs'])
        y_med = sum(info['ys']) / len(info['ys'])
        caixas_lidas.append({'x_rel': x_med / largura_img, 'y_rel': y_med / altura_img, 'txt': texto_bloco})

    caixas_lidas.sort(key=lambda c: c['y_rel'])
    cab_partes = [c['txt'] for c in caixas_lidas if c['y_rel'] < 0.065]
    cabecalho = " ".join(cab_partes) if cab_partes else "Fluxograma Importado"

    ignorar = {"processo", "método", "metodo", "responsável", "responsavel", "frequência", "frequencia", "registro", "início", "inicio", "fim", "sim", "não", "nao"}

    itens_etapas = []
    for c in caixas_lidas:
        if c['y_rel'] < 0.11 or c['txt'].lower() in ignorar:
            continue
        xr = c['x_rel']
        col = 0 if xr < 0.25 else (1 if xr < 0.51 else (2 if xr < 0.67 else (3 if xr < 0.82 else 4)))
        itens_etapas.append({'col': col, 'y_rel': c['y_rel'], 'txt': c['txt']})

    linhas_agrupadas = []
    for item in itens_etapas:
        alocado = False
        for l_agr in linhas_agrupadas:
            if abs(l_agr['y_ref'] - item['y_rel']) < 0.038:
                if l_agr['cols'][item['col']]:
                    l_agr['cols'][item['col']] += " " + item['txt']
                else:
                    l_agr['cols'][item['col']] = item['txt']
                alocado = True
                break
        if not alocado:
            novas_cols = ["", "", "", "", ""]
            novas_cols[item['col']] = item['txt']
            linhas_agrupadas.append({'y_ref': item['y_rel'], 'cols': novas_cols})

    linhas_agrupadas.sort(key=lambda l: l['y_ref'])
    linhas_texto = []
    for idx_l, l_agr in enumerate(linhas_agrupadas):
        cols = l_agr['cols']
        if not any(cols):
            continue
        if '?' in cols[0] and not cols[1]:
            cols[1] = "[PULAR]" if idx_l > len(linhas_agrupadas) // 2 else "[VOLTAR]"
        linhas_texto.append(" | ".join(cols))

    return cabecalho, "\n".join(linhas_texto)

def importar_arquivo_fluxo(nome_arq, conteudo_bytes):
    nome_lower = nome_arq.lower()
    texto_bruto = conteudo_bytes.decode('latin-1', errors='ignore')
    cab, etp = desempacotar_metadados(texto_bruto)
    if cab is not None:
        return cab, etp, "Metadados Embutidos (100% Exato)"

    if nome_lower.endswith('.pdf'):
        try:
            reader = PdfReader(io.BytesIO(conteudo_bytes))
            meta = reader.metadata
            if meta:
                for val in meta.values():
                    cab, etp = desempacotar_metadados(val)
                    if cab is not None:
                        return cab, etp, "Metadados PDF (100% Exato)"
        except Exception:
            pass
    elif nome_lower.endswith('.png'):
        try:
            img = Image.open(io.BytesIO(conteudo_bytes))
            for val in img.info.values():
                cab, etp = desempacotar_metadados(val)
                if cab is not None:
                    return cab, etp, "Metadados PNG (100% Exato)"
        except Exception:
            pass

    try:
        if nome_lower.endswith('.pdf'):
            paginas = convert_from_bytes(conteudo_bytes, dpi=250)
            if paginas:
                cab, etp = reconstruir_fluxo_via_ocr(paginas[0])
                if etp:
                    return cab, etp, "Reconhecimento Visual OCR (PDF Antigo)"
        elif nome_lower.endswith('.png'):
            img = Image.open(io.BytesIO(conteudo_bytes)).convert('RGB')
            cab, etp = reconstruir_fluxo_via_ocr(img)
            if etp:
                return cab, etp, "Reconhecimento Visual OCR (Imagem PNG)"
    except Exception:
        pass

    return None, None, "Não foi possível extrair etapas deste arquivo."


# ==============================================================================
# MOTOR GRÁFICO (MATPLOTLIB)
# ==============================================================================

CORES_CONECTORES = ['#031b29', '#086799', '#1b4d3e', '#5b2c6f', '#78281f', '#b9770e']

def desenhar_conector_circular(ax, x_c, y_c, letra, cor_fundo='#031b29'):
    ax.scatter([x_c + 0.018], [y_c - 0.022], s=440, c='#bbbbbb', edgecolors='none', alpha=0.65, zorder=4.2)
    ax.scatter([x_c], [y_c], s=440, c=cor_fundo, edgecolors='black', linewidths=1.3, zorder=4.5)
    ax.text(x_c, y_c, letra, ha='center', va='center', color='white', fontsize=9.5, fontweight='bold', fontname='Arial', zorder=5)

def desenhar_flamula(ax, x_c, y_c, rotulo, cor_fundo='#031b29'):
    hw, hh = 0.14, 0.14
    sombra_verts = [(x_c - hw + 0.018, y_c + hh - 0.022), (x_c + hw + 0.018, y_c + hh - 0.022), (x_c + hw + 0.018, y_c - 0.022), (x_c + 0.018, y_c - hh - 0.032), (x_c - hw + 0.018, y_c - 0.022)]
    ax.add_patch(patches.Polygon(sombra_verts, closed=True, facecolor='#bbbbbb', edgecolor='none', alpha=0.6, zorder=4.2))
    verts = [(x_c - hw, y_c + hh), (x_c + hw, y_c + hh), (x_c + hw, y_c - 0.02), (x_c, y_c - hh), (x_c - hw, y_c - 0.02)]
    ax.add_patch(patches.Polygon(verts, closed=True, facecolor=cor_fundo, edgecolor='black', linewidth=1.2, zorder=4.5))
    ax.text(x_c, y_c + 0.02, str(rotulo), ha='center', va='center', color='white', fontsize=9, fontweight='bold', fontname='Arial', zorder=5)

def desenhar_espera(ax, x_center, y_center, w, h, lw=1.2):
    x0, x1 = x_center - w / 2, x_center + w / 2
    y0, y1 = y_center - h / 2, y_center + h / 2
    raio = h / 2.0
    verts = [(x0, y1), (x1 - raio, y1), (x1, y1), (x1, y_center), (x1, y0), (x1 - raio, y0), (x0, y0), (x0, y1)]
    codes = [Path.MOVETO, Path.LINETO, Path.CURVE4, Path.CURVE4, Path.CURVE4, Path.CURVE4, Path.LINETO, Path.CLOSEPOLY]
    ax.add_patch(patches.PathPatch(Path(verts, codes), facecolor='white', edgecolor='black', lw=lw, zorder=3))

def desenhar_modular(ax, x_center, y_center, w, h, lw=1.2):
    x0, x1 = x_center - w / 2, x_center + w / 2
    y0, y1 = y_center - h / 2, y_center + h / 2
    larg_barra = 0.18
    ax.add_patch(patches.Rectangle((x0, y0), w, h, facecolor='white', edgecolor='black', lw=lw, zorder=3))
    ax.plot([x0 + larg_barra, x0 + larg_barra], [y0, y1], color='black', lw=lw, zorder=3.5)
    ax.plot([x1 - larg_barra, x1 - larg_barra], [y0, y1], color='black', lw=lw, zorder=3.5)

def interpretar_regra_decisao(met_raw, idx, n_etapas):
    txt = met_raw.upper().strip()
    forcar_conector = ('CONECTOR' in txt) or ('EXTERNO' in txt) or ('PAGINA' in txt)
    letra_manual = None
    m_letra = re.search(r':([A-Z0-9]+)\b', txt)
    if m_letra and m_letra.group(1) not in ['V', 'P', 'C']:
        letra_manual = m_letra.group(1)
        forcar_conector = True

    if 'PULAR' in txt or '<+' in txt:
        m_num = re.search(r'(?:PULAR|<\+)\s*:?\s*(\d+)', txt)
        salto = int(m_num.group(1)) if m_num else 2
        idx_dest = min(idx + salto, n_etapas - 1)
        direcao = 'PULAR'
    else:
        m_num = re.search(r'(?:VOLTAR|<-)\s*:?\s*(\d+)', txt)
        salto = int(m_num.group(1)) if m_num else 1
        idx_dest = max(idx - salto, 0)
        direcao = 'VOLTAR'

    return direcao, idx_dest, abs(idx_dest - idx), forcar_conector, letra_manual

def desenhar_documento(ax, x_center, y_center, w, h, lw=1.2):
    x0, x1 = x_center - w / 2, x_center + w / 2
    y0, y1 = y_center - h / 2, y_center + h / 2
    amp = min(h * 0.12, 0.08)
    verts = [(x0, y1), (x1, y1), (x1, y0 + amp), (x_center + w * 0.25, y0 + amp * 2.2), (x_center, y0 + amp * 0.5), (x_center - w * 0.25, y0 - amp * 1.2), (x0, y0 + amp), (x0, y1)]
    codes = [Path.MOVETO, Path.LINETO, Path.LINETO, Path.CURVE3, Path.CURVE3, Path.CURVE3, Path.CURVE3, Path.CLOSEPOLY]
    ax.add_patch(patches.PathPatch(Path(verts, codes), facecolor='white', edgecolor='black', lw=lw, zorder=3))

def desenhar_pilula(ax, x_center, y_center, w, h, texto, fontsize=9, lw=1.2):
    patch = patches.FancyBboxPatch((x_center - w/2, y_center - h/2), w, h, boxstyle=f"round,pad=0,rounding_size={h/2}", facecolor='white', edgecolor='black', lw=lw, zorder=3)
    ax.add_patch(patch)
    ax.text(x_center, y_center, texto, ha='center', va='center', fontsize=fontsize, fontname='Arial', color='black', zorder=4)

def calcular_texto_e_altura(texto, largura_chars, h_min=0.54, altura_por_linha=0.15, padding_vertical=0.22):
    if not texto or not texto.strip():
        return "", h_min
    linhas = textwrap.wrap(texto.strip(), width=largura_chars)
    return "\n".join(linhas), max(h_min, (len(linhas) * altura_por_linha) + padding_vertical)

def gerar_figura_fluxograma(titulo_cabecalho, texto_etapas, modo_conector="SEMPRE"):
    linhas_brutas = [l.strip() for l in texto_etapas.strip().split('\n') if l.strip()]
    if not linhas_brutas:
        return None

    etapas = []
    for l in linhas_brutas:
        partes = [p.strip() for p in l.split('|')]
        while len(partes) < 5:
            partes.append("")
        etapas.append(partes[:5])

    n_etapas = len(etapas)
    total_decisoes = sum(1 for (proc, _, _, _, _) in etapas if '?' in proc)

    w_proc, chars_proc = 1.20, 16
    w_met, chars_met = 1.95, 28
    w_resp, chars_resp = 1.02, 14
    w_freq, chars_freq = 0.92, 13
    w_reg, chars_reg = 0.96, 13

    h_min_padrao = 0.54
    h_min_losango = 0.80
    gap_entre_linhas = 0.62

    dados_processados = []
    y_inicio = -1.55
    cursor_y = y_inicio - 0.16 - gap_entre_linhas

    for idx, (proc, met, resp, freq, reg) in enumerate(etapas):
        eh_decisao = '?' in proc
        eh_sub = proc.upper().startswith('[SUB]')
        eh_espera = proc.upper().startswith('[ESPERA]')
        eh_modular = proc.upper().startswith('[REF]') or proc.upper().startswith('[MODULAR]')
        texto_proc_limpo = re.sub(r'^\[(SUB|ESPERA|REF|MODULAR)\]\s*', '', proc, flags=re.IGNORECASE)

        if eh_decisao:
            txt_p, h_p = calcular_texto_e_altura(texto_proc_limpo, 13, h_min=h_min_losango, altura_por_linha=0.16, padding_vertical=0.38)
            dados_processados.append({'tipo': 'DECISAO', 'yc': cursor_y - h_p/2, 'h_linha': h_p, 'w_p': max(w_proc, h_p * 1.40), 'proc': (txt_p, h_p), 'met_raw': met})
        else:
            larg_chars_p = 12 if (eh_sub or eh_espera or eh_modular) else chars_proc
            txt_p, h_p = calcular_texto_e_altura(texto_proc_limpo, larg_chars_p, h_min=h_min_padrao)
            txt_m, h_m = calcular_texto_e_altura(met, chars_met, h_min=h_min_padrao)
            txt_r, h_r = calcular_texto_e_altura(resp, chars_resp, h_min=h_min_padrao)
            txt_f, h_f = calcular_texto_e_altura(freq, chars_freq, h_min=h_min_padrao)
            txt_g, h_g = calcular_texto_e_altura(reg, chars_reg, h_min=h_min_padrao + 0.06)

            tem_laterais = any([met.strip(), resp.strip(), freq.strip(), reg.strip()])
            h_linha = max([h_p, h_m, h_r, h_f, h_g]) if tem_laterais else h_p
            tipo_caixa = 'ESPERA' if eh_espera else ('MODULAR' if eh_modular else ('SUB' if eh_sub else 'NORMAL'))

            dados_processados.append({'tipo': tipo_caixa, 'yc': cursor_y - h_linha/2, 'h_linha': h_linha, 'tem_laterais': tem_laterais, 'proc': (txt_p, h_p), 'met': (txt_m, h_m), 'resp': (txt_r, h_r), 'freq': (txt_f, h_f), 'reg': (txt_g, h_g)})

        cursor_y = cursor_y - h_linha - gap_entre_linhas

    y_fim = cursor_y - 0.16
    altura_grade = abs(y_fim) + 0.85
    fig, ax = plt.subplots(figsize=(11, altura_grade * 0.95), dpi=300, facecolor='white')
    ax.set_facecolor('white')
    ax.set_xlim(0, 10.0)
    ax.set_ylim(-altura_grade, 0)
    ax.axis('off')

    y_topo = 0.0
    y_base_titulo = -0.55
    y_base_colunas = -1.05
    y_base_tabela = -altura_grade + 0.15

    ax.add_patch(patches.Rectangle((0.1, y_base_tabela), 9.8, abs(y_base_tabela), facecolor='white', edgecolor='black', lw=1.2, zorder=1))
    ax.plot([0.1, 9.9], [y_base_titulo, y_base_titulo], color='black', lw=1.2, zorder=1)
    ax.plot([0.38, 9.9], [y_base_colunas, y_base_colunas], color='black', lw=1.2, zorder=1)

    x_divs = [0.1, 0.38, 2.60, 5.20, 6.75, 8.28, 9.9]
    for x in x_divs[1:-1]:
        ax.plot([x, x], [y_base_titulo, y_base_tabela], color='black', lw=1.2, zorder=1)

    ax.text(5.0, (y_topo + y_base_titulo)/2, titulo_cabecalho, ha='center', va='center', fontsize=10.5, fontname='Arial', color='black', zorder=4)

    nomes_colunas = ["Processo", "Método", "Responsável", "Frequência", "Registro"]
    centros_x = [(x_divs[i+1] + x_divs[i+2])/2 for i in range(5)]
    x_proc = centros_x[0] + 0.22

    for i, nome in enumerate(nomes_colunas):
        ax.text(centros_x[i], (y_base_titulo + y_base_colunas)/2, nome, ha='center', va='center', fontsize=9.5, fontname='Arial', color='black', zorder=4)

    desenhar_pilula(ax, x_proc, y_inicio, 0.92, 0.32, "Início", fontsize=9)
    limites_y = []

    for item in dados_processados:
        yc = item['yc']
        tipo = item['tipo']
        txt_p, h_p = item['proc']

        if tipo == 'DECISAO':
            w_los = item['w_p']
            verts = [(x_proc, yc + h_p/2), (x_proc + w_los/2, yc), (x_proc, yc - h_p/2), (x_proc - w_los/2, yc)]
            ax.add_patch(patches.Polygon(verts, closed=True, facecolor='white', edgecolor='black', lw=1.2, zorder=3))
            ax.text(x_proc, yc, txt_p, ha='center', va='center', fontsize=7.5, fontname='Arial', color='black', zorder=4)
            limites_y.append((yc + h_p/2, yc - h_p/2))
        elif tipo == 'ESPERA':
            desenhar_espera(ax, x_proc, yc, w_proc, h_p)
            ax.text(x_proc - 0.05, yc, txt_p, ha='center', va='center', fontsize=7.5, fontname='Arial', color='black', zorder=4)
            limites_y.append((yc + h_p/2, yc - h_p/2))
        elif tipo == 'MODULAR':
            desenhar_modular(ax, x_proc, yc, w_proc, h_p)
            ax.text(x_proc, yc, txt_p, ha='center', va='center', fontsize=7.5, fontname='Arial', color='black', zorder=4)
            limites_y.append((yc + h_p/2, yc - h_p/2))
        else:
            ax.add_patch(patches.Rectangle((x_proc - w_proc/2, yc - h_p/2), w_proc, h_p, facecolor='white', edgecolor='black', lw=1.2, zorder=3))
            if tipo == 'SUB':
                ax.plot([x_proc - w_proc/2 + 0.14, x_proc - w_proc/2 + 0.14], [yc - h_p/2, yc + h_p/2], color='black', lw=1.2, zorder=3.5)
                ax.plot([x_proc + w_proc/2 - 0.14, x_proc + w_proc/2 - 0.14], [yc - h_p/2, yc + h_p/2], color='black', lw=1.2, zorder=3.5)
            ax.text(x_proc, yc, txt_p, ha='center', va='center', fontsize=7.5, fontname='Arial', color='black', zorder=4)
            limites_y.append((yc + h_p/2, yc - h_p/2))

        if tipo in ['ESPERA', 'MODULAR', 'SUB', 'NORMAL'] and item['tem_laterais']:
            txt_m, h_m = item['met']
            txt_r, h_r = item['resp']
            txt_f, h_f = item['freq']
            txt_g, h_g = item['reg']
            ax.plot([x_proc + w_proc/2, centros_x[4] - w_reg/2], [yc, yc], color='black', lw=1.1, linestyle=(0, (5, 4)), zorder=2)
            ax.add_patch(patches.Rectangle((centros_x[1] - w_met/2, yc - h_m/2), w_met, h_m, facecolor='white', edgecolor='black', lw=1.2, zorder=3))
            ax.text(centros_x[1], yc, txt_m, ha='center', va='center', fontsize=7.5, fontname='Arial', color='black', zorder=4)
            ax.add_patch(patches.Rectangle((centros_x[2] - w_resp/2, yc - h_r/2), w_resp, h_r, facecolor='white', edgecolor='black', lw=1.2, zorder=3))
            ax.text(centros_x[2], yc, txt_r, ha='center', va='center', fontsize=7.5, fontname='Arial', color='black', zorder=4)
            ax.add_patch(patches.Rectangle((centros_x[3] - w_freq/2, yc - h_f/2), w_freq, h_f, facecolor='white', edgecolor='black', lw=1.2, zorder=3))
            ax.text(centros_x[3], yc, txt_f, ha='center', va='center', fontsize=7.5, fontname='Arial', color='black', zorder=4)
            desenhar_documento(ax, centros_x[4], yc, w_reg, h_g)
            ax.text(centros_x[4], yc + 0.03, txt_g, ha='center', va='center', fontsize=7.5, fontname='Arial', color='black', zorder=4)

    desenhar_pilula(ax, x_proc, y_fim, 0.92, 0.32, "Fim", fontsize=9)
    estilo_seta = dict(arrowstyle='-|>,head_length=0.35,head_width=0.22', color='black', lw=1.1, shrinkA=0, shrinkB=0)

    if n_etapas > 0:
        ax.annotate('', xy=(x_proc, limites_y[0][0]), xytext=(x_proc, y_inicio - 0.16), arrowprops=estilo_seta, zorder=4)

    contador_conector = 0
    raio_visual = 0.16
    chegadas_por_destino = {}

    for idx, item in enumerate(dados_processados):
        yc = item['yc']
        topo_atual, base_atual = limites_y[idx]
        topo_prox = limites_y[idx + 1][0] if idx < n_etapas - 1 else y_fim + 0.16

        ax.annotate('', xy=(x_proc, topo_prox), xytext=(x_proc, base_atual), arrowprops=estilo_seta, zorder=4)

        if item['tipo'] == 'DECISAO':
            direcao, idx_destino, distancia, forcar_con, letra_man = interpretar_regra_decisao(item['met_raw'], idx, n_etapas)
            w_los = item['w_p']
            x_ponta_esq = x_proc - w_los / 2
            x_entrada_esq = x_proc - w_proc / 2

            usar_conector = True if modo_conector == "SEMPRE" else (False if modo_conector == "NUNCA" else (total_decisoes > 2 or distancia > 1 or forcar_con))
            ax.text(x_proc + 0.14, (base_atual + topo_prox) / 2, "Sim", ha='left', va='center', fontsize=8.5, fontname='Arial', color='black')

            if usar_conector:
                cor_atual = CORES_CONECTORES[contador_conector % len(CORES_CONECTORES)]
                eh_externo = bool(letra_man and letra_man.isdigit()) or ('EXTERNO' in item['met_raw'].upper())
                rotulo = letra_man if letra_man else (str(contador_conector + 1) if eh_externo else string.ascii_uppercase[contador_conector % 26])
                contador_conector += 1

                x_con_saida = x_ponta_esq - 0.42
                ax.text((x_ponta_esq + x_con_saida + raio_visual) / 2, yc + 0.11, "Não", ha='center', va='bottom', fontsize=8.5, fontname='Arial', color='black')
                ax.annotate('', xy=(x_con_saida + raio_visual, yc), xytext=(x_ponta_esq, yc), arrowprops=estilo_seta, zorder=4)

                if str(rotulo).isdigit():
                    desenhar_flamula(ax, x_con_saida, yc, rotulo, cor_fundo=cor_atual)
                else:
                    desenhar_conector_circular(ax, x_con_saida, yc, rotulo, cor_fundo=cor_atual)
                    n_prev = chegadas_por_destino.get(idx_destino, 0)
                    chegadas_por_destino[idx_destino] = n_prev + 1
                    y_destino = dados_processados[idx_destino]['yc'] + (n_prev * 0.12)
                    x_con_chegada = x_entrada_esq - 0.30 - (n_prev * 0.24)
                    y_con_chegada = limites_y[idx_destino][0] + 0.22
                    desenhar_conector_circular(ax, x_con_chegada, y_con_chegada, rotulo, cor_fundo=cor_atual)
                    ax.plot([x_con_chegada, x_con_chegada], [y_con_chegada - raio_visual, y_destino], color='black', lw=1.2, zorder=3.5)
                    ax.annotate('', xy=(x_entrada_esq, y_destino), xytext=(x_con_chegada, y_destino), arrowprops=estilo_seta, zorder=4)
            else:
                x_canal = x_proc - max(w_proc, w_los) / 2 - 0.24
                ax.text(x_canal if direcao == 'PULAR' else x_proc - 0.34, yc + (0.12 if direcao == 'PULAR' else 0.32), "Não", ha='center' if direcao == 'PULAR' else 'right', va='bottom' if direcao == 'PULAR' else 'center', fontsize=8.5, fontname='Arial', color='black')
                y_destino = dados_processados[idx_destino]['yc']
                ax.plot([x_ponta_esq, x_canal, x_canal], [yc, yc, y_destino], color='black', lw=1.1, zorder=3)
                ax.annotate('', xy=(x_entrada_esq, y_destino), xytext=(x_canal, y_destino), arrowprops=estilo_seta, zorder=4)

    plt.tight_layout()
    return fig, titulo_cabecalho, texto_etapas


# ==============================================================================
# INTERFACE DO STREAMLIT (SITE WEB)
# ==============================================================================

st.title("📋 Gerador e Editor de Fluxogramas Operacionais")
st.markdown("Ferramenta web para criação, edição e exportação de fluxogramas no padrão corporativo.")

# Barra lateral para Importação / Exportação
st.sidebar.header("🔄 Importar / Carregar Fluxo")
arquivo_carregado = st.sidebar.file_uploader("Envie um arquivo (.PDF, .PNG ou .SVG)", type=['pdf', 'png', 'svg'])

cabecalho_inicial = "Abertura, Operação de Caixa e Atendimento – Setor de Caixa – Guga Material de Construção"
etapas_iniciais = """Abrir terminal de caixa | Conferir fundo de troco e iniciar sistema | Operador de Caixa | Diária | Termo de Abertura
[ESPERA] Aguardar liberação de supervisor | Solicitar autorização via sistema interno | Operador de Caixa | Por Demanda | Tela de Liberação
[REF] Registrar Contas a Pagar | Consultar POP financeiro de lançamento de despesas | Operador de Caixa | Por Venda | Sistema Financeiro
Localizar pedido no sistema | Receber número da ficha do cliente e consultar pré-venda | Operador de Caixa | Por Venda | Tela de Pedido
Forma de pagamento correta? | [VOLTAR:1] | | |
Receber pagamento | Processar transação (PIX, cartão ou espécie) | Operador de Caixa | Por Venda | Comprovante
Emitir e entregar Nota Fiscal | Autorizar emissão no sistema | Operador de Caixa | Por Venda | Nota Fiscal
Pedido possui entrega? | [PULAR:2:1] | | |
Emitir e arquivar guia de entrega | Imprimir guia e guardar no setor | Operador de Caixa | Por Demanda | Guia de Entrega
Finalizar atendimento | Orientar cliente a retirar mercadoria na expedição | Operador de Contínua | Pedido Finalizado"""

if arquivo_carregado is not None:
    bytes_arq = arquivo_carregado.read()
    cab_ lido, etp_lido, msg = importar_arquivo_fluxo(arquivo_carregado.name, bytes_arq)
    if etp_lido:
        cabecalho_inicial = cab_lido
        etapas_iniciais = etp_lido
        st.sidebar.success(f"✅ Arquivo carregado via {msg}!")
    else:
        st.sidebar.error(f"⚠️ {msg}")

# Campos principais na tela
st.subheader("ETAPA 1: Cabeçalho Superior")
input_cabecalho = st.text_input("Identificação do Cabeçalho:", value=cabecalho_inicial)

col1, col2 = st.columns(2)
with col1:
    input_arquivo = st.text_input("Nome do arquivo para salvamento:", value="fluxo_operacional")
with col2:
    seletor_conectores = st.selectbox(
        "Modo de Conectores de Desvio:",
        options=[('SEMPRE', 'Usar Conectores (A, B, C) em TODAS as decisões'),
                 ('AUTO', 'Automático (> 2 decisões ou saltos longos)'),
                 ('NUNCA', 'Apenas Linhas Contínuas (Sem conectores)')],
        format_func=lambda x: x[1]
    )[0]

st.subheader("ETAPA 2: Etapas do Fluxograma")
st.markdown("<small>Formato: <code>Processo | Método | Responsável | Frequência | Registro</code> (Tags: <code>[ESPERA]</code>, <code>[REF]</code>, <code>[SUB]</code>, <code>?</code>)</small>", unsafe_allow_html=True)
input_etapas = st.text_area("Insira as linhas do fluxo:", value=etapas_iniciais, height=260)

if st.button("🚀 Gerar e Visualizar Fluxograma", type="primary"):
    fig, cab, etp = gerar_figura_fluxograma(input_cabecalho, input_etapas, modo_conector=seletor_conectores)
    if fig:
        st.pyplot(fig)
        
        # Geração dos arquivos para download
        nome_limpo = re.sub(r'[^\w\-_]', '_', input_arquivo.strip()) or "fluxograma"
        assinatura = empacotar_metadados(cab, etp)

        # PDF Bytes
        buf_pdf = io.BytesIO()
        fig.savefig(buf_pdf, format='pdf', bbox_inches='tight', facecolor='white')
        buf_pdf.seek(0)
        reader_temp = PdfReader(buf_pdf)
        writer = PdfWriter()
        for page in reader_temp.pages:
            writer.add_page(page)
        writer.add_metadata({'/Subject': assinatura, '/Keywords': assinatura, '/Title': cab})
        pdf_final_bytes = io.BytesIO()
        writer.write(pdf_final_bytes)
        pdf_final_bytes.seek(0)

        # PNG Bytes
        buf_png = io.BytesIO()
        fig.savefig(buf_png, format='png', dpi=300, bbox_inches='tight', facecolor='white')
        buf_png.seek(0)
        img_pil = Image.open(buf_png)
        meta_png = PngImagePlugin.PngInfo()
        meta_png.add_text("Description", assinatura)
        png_final_bytes = io.BytesIO()
        img_pil.save(png_final_bytes, "PNG", pnginfo=meta_png)
        png_final_bytes.seek(0)

        st.success("✅ Fluxograma gerado com sucesso! Baixe os arquivos prontos abaixo:")
        
        c_dl1, c_dl2 = st.columns(2)
        with c_dl1:
            st.download_button(
                label="📥 Baixar Arquivo .PDF",
                data=pdf_final_bytes,
                file_name=f"{nome_limpo}.pdf",
                mime="application/pdf"
            )
        with c_dl2:
            st.download_button(
                label="📥 Baixar Imagem .PNG",
                data=png_final_bytes,
                file_name=f"{nome_limpo}.png",
                mime="image/png"
            )