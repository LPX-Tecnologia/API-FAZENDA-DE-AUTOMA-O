from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from playwright.sync_api import sync_playwright
import json
import os
import tempfile
import threading
import queue
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
CORS(app)

DB_FILE = 'automacoes.json'
CONTAS_FILE = 'contas.json'
UPLOAD_FOLDER = tempfile.mkdtemp()

# Fila de tarefas
task_queue = queue.Queue()
resultados = {}
MAX_TELAS = int(os.environ.get('MAX_TELAS', '10'))  # Aumente conforme seu servidor

def carregar(arquivo):
    if os.path.exists(arquivo):
        with open(arquivo, 'r') as f:
            return json.load(f)
    return []

def salvar(arquivo, dados):
    with open(arquivo, 'w') as f:
        json.dump(dados, f, indent=2)

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "api": "Clipador - Fazenda de Automação",
        "versao": "3.0.0",
        "max_telas_simultaneas": MAX_TELAS,
        "recursos": [
            "criar_automacao", "executar_automacao",
            "multiplicar_telas", "gerenciar_contas",
            "agendar_execucao", "ver_resultados",
            "pausar_automacao", "exportar_relatorio"
        ]
    })

# ==========================================================
# GERENCIAR CONTAS
# ==========================================================

@app.route('/api/contas', methods=['GET'])
def listar_contas():
    """Lista todas as contas cadastradas"""
    contas = carregar(CONTAS_FILE)
    return jsonify({
        "total": len(contas),
        "contas": contas
    })

@app.route('/api/contas', methods=['POST'])
def adicionar_contas():
    """Adiciona contas em lote"""
    data = request.json
    novas_contas = data.get('contas', [])
    
    contas = carregar(CONTAS_FILE)
    
    for conta in novas_contas:
        conta['id'] = len(contas) + 1
        conta['status'] = 'ativa'
        conta['criada_em'] = str(datetime.now())
        contas.append(conta)
    
    salvar(CONTAS_FILE, contas)
    
    return jsonify({
        "adicionadas": len(novas_contas),
        "total_contas": len(contas)
    }), 201

# ==========================================================
# CRIAR AUTOMAÇÃO (GRAVAR PASSOS)
# ==========================================================

@app.route('/api/automacao/criar', methods=['POST'])
def criar_automacao():
    """Cria uma nova automação (script do que fazer)"""
    data = request.json
    
    automacao = {
        "id": len(carregar(DB_FILE)) + 1,
        "nome": data.get('nome', 'Automação ' + str(datetime.now())),
        "url_alvo": data.get('url_alvo', ''),
        "passos": data.get('passos', []),
        "criada_em": str(datetime.now()),
        "status": "criada"
    }
    
    automacoes = carregar(DB_FILE)
    automacoes.append(automacao)
    salvar(DB_FILE, automacoes)
    
    return jsonify(automacao), 201

# ==========================================================
# EXECUTAR AUTOMAÇÃO (MÚLTIPLAS TELAS)
# ==========================================================

@app.route('/api/automacao/executar', methods=['POST'])
def executar_automacao():
    """Executa automação em múltiplas telas"""
    data = request.json
    automacao_id = data.get('automacao_id')
    quantidade_telas = min(data.get('quantidade_telas', 1), MAX_TELAS)
    conta_inicial = data.get('conta_inicial', 0)
    
    automacoes = carregar(DB_FILE)
    automacao = next((a for a in automacoes if a['id'] == automacao_id), None)
    
    if not automacao:
        return jsonify({"erro": "Automação não encontrada"}), 404
    
    contas = carregar(CONTAS_FILE)
    contas_ativas = [c for c in contas if c['status'] == 'ativa']
    
    if len(contas_ativas) < quantidade_telas:
        return jsonify({"erro": f"Contas insuficientes. Você tem {len(contas_ativas)} contas ativas."}), 400
    
    # Seleciona contas para esta execução
    contas_selecionadas = contas_ativas[conta_inicial:conta_inicial + quantidade_telas]
    
    # ID da execução
    execucao_id = str(int(time.time()))
    resultados[execucao_id] = {
        "automacao_id": automacao_id,
        "total_telas": quantidade_telas,
        "concluidas": 0,
        "falhas": 0,
        "resultados": [],
        "status": "executando",
        "inicio": str(datetime.now())
    }
    
    # Inicia em background
    thread = threading.Thread(
        target=executar_em_massa,
        args=(execucao_id, automacao, contas_selecionadas)
    )
    thread.start()
    
    return jsonify({
        "execucao_id": execucao_id,
        "total_telas": quantidade_telas,
        "contas_utilizadas": [c['email'] for c in contas_selecionadas],
        "status": "executando"
    })

def executar_em_massa(execucao_id, automacao, contas):
    """Executa automação em todas as telas"""
    resultados_execucao = resultados[execucao_id]
    
    with ThreadPoolExecutor(max_workers=MAX_TELAS) as executor:
        futures = []
        for i, conta in enumerate(contas):
            future = executor.submit(
                executar_uma_tela,
                automacao,
                conta,
                i + 1
            )
            futures.append(future)
        
        for future in as_completed(futures):
            try:
                resultado = future.result()
                resultados_execucao['resultados'].append(resultado)
                if resultado['sucesso']:
                    resultados_execucao['concluidas'] += 1
                else:
                    resultados_execucao['falhas'] += 1
            except Exception as e:
                resultados_execucao['falhas'] += 1
                resultados_execucao['resultados'].append({
                    "sucesso": False,
                    "erro": str(e)
                })
    
    resultados_execucao['status'] = 'concluida'
    resultados_execucao['fim'] = str(datetime.now())

def executar_uma_tela(automacao, conta, numero_tela):
    """Executa automação em UMA tela"""
    resultado = {
        "tela_numero": numero_tela,
        "conta": conta['email'],
        "inicio": str(datetime.now()),
        "sucesso": False
    }
    
    try:
        with sync_playwright() as p:
            # Lança navegador
            browser = p.chromium.launch(headless=True)
            
            # Configura viewport de celular
            context = browser.new_context(
                viewport={'width': 375, 'height': 812},
                user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)'
            )
            
            page = context.new_page()
            
            # Acessa URL alvo
            page.goto(automacao['url_alvo'], wait_until='networkidle')
            
            # Executa cada passo da automação
            for passo in automacao.get('passos', []):
                acao = passo.get('acao')
                seletor = passo.get('seletor')
                valor = passo.get('valor', '')
                
                # Substitui variáveis {{email}} {{senha}} pelos dados da conta
                if valor and '{{' in valor:
                    valor = valor.replace('{{email}}', conta.get('email', ''))
                    valor = valor.replace('{{senha}}', conta.get('senha', ''))
                    valor = valor.replace('{{nome}}', conta.get('nome', ''))
                
                if acao == 'clicar':
                    page.click(seletor)
                    page.wait_for_timeout(1000)
                
                elif acao == 'preencher':
                    page.fill(seletor, valor)
                    page.wait_for_timeout(500)
                
                elif acao == 'esperar':
                    page.wait_for_timeout(int(valor))
                
                elif acao == 'navegar':
                    page.goto(valor)
                    page.wait_for_timeout(2000)
                
                elif acao == 'screenshot':
                    temp_file = os.path.join(UPLOAD_FOLDER, f'tela_{numero_tela}_{datetime.now().timestamp()}.png')
                    page.screenshot(path=temp_file)
                    resultado['screenshot'] = temp_file
                
                elif acao == 'scroll':
                    page.evaluate(f'window.scrollBy(0, {valor})')
                    page.wait_for_timeout(1000)
            
            resultado['sucesso'] = True
            resultado['url_final'] = page.url
            
            browser.close()
    
    except Exception as e:
        resultado['sucesso'] = False
        resultado['erro'] = str(e)
    
    resultado['fim'] = str(datetime.now())
    return resultado

# ==========================================================
# VER RESULTADOS
# ==========================================================

@app.route('/api/automacao/resultados/<execucao_id>', methods=['GET'])
def ver_resultados(execucao_id):
    """Ver resultados de uma execução"""
    if execucao_id not in resultados:
        return jsonify({"erro": "Execução não encontrada"}), 404
    
    return jsonify(resultados[execucao_id])

@app.route('/api/automacao/historico', methods=['GET'])
def historico_automacoes():
    """Histórico de todas as automações"""
    return jsonify({
        "automacoes": carregar(DB_FILE),
        "execucoes_recentes": {k: v for k, v in list(resultados.items())[-10:]}
    })

# ==========================================================
# MODELOS DE AUTOMAÇÃO
# ==========================================================

@app.route('/api/automacao/modelos', methods=['GET'])
def modelos_automacao():
    """Modelos prontos de automação"""
    return jsonify({
        "modelos": [
            {
                "nome": "Login em Site",
                "descricao": "Faz login automaticamente em qualquer site",
                "passos": [
                    {"acao": "navegar", "valor": "{{URL_DO_SITE}}"},
                    {"acao": "esperar", "valor": "2000"},
                    {"acao": "preencher", "seletor": "#email", "valor": "{{email}}"},
                    {"acao": "preencher", "seletor": "#senha", "valor": "{{senha}}"},
                    {"acao": "clicar", "seletor": "#btnLogin"},
                    {"acao": "esperar", "valor": "3000"},
                    {"acao": "screenshot", "valor": ""}
                ]
            },
            {
                "nome": "Curtir Posts",
                "descricao": "Navega e curte posts (uso legítimo)",
                "passos": [
                    {"acao": "navegar", "valor": "{{URL_REDE_SOCIAL}}"},
                    {"acao": "esperar", "valor": "3000"},
                    {"acao": "clicar", "seletor": "[aria-label='Curtir']"},
                    {"acao": "esperar", "valor": "1000"},
                    {"acao": "scroll", "valor": "500"},
                    {"acao": "esperar", "valor": "2000"}
                ]
            },
            {
                "nome": "Teste de App",
                "descricao": "Testa funcionalidades do seu app",
                "passos": [
                    {"acao": "navegar", "valor": "{{URL_DO_APP}}"},
                    {"acao": "esperar", "valor": "2000"},
                    {"acao": "preencher", "seletor": "#loginEmail", "valor": "{{email}}"},
                    {"acao": "preencher", "seletor": "#loginSenha", "valor": "{{senha}}"},
                    {"acao": "clicar", "seletor": "#btnLogin"},
                    {"acao": "esperar", "valor": "3000"},
                    {"acao": "screenshot", "valor": ""}
                ]
            }
        ]
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5022))
    app.run(host='0.0.0.0', port=port, debug=False)
