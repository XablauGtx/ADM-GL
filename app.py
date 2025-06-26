import io
import json
import os  # <-- adicionado
import re
import csv
from datetime import date, datetime, timezone
from functools import wraps

import firebase_admin
import requests
from firebase_admin import credentials, firestore
from flask import (Flask, Response, flash, redirect, render_template, request,
                   send_file, session, url_for)

# --- INICIALIZAÇÃO DO FIREBASE ---
try:
    firebase_config = json.loads(os.environ["FIREBASE_CREDENTIALS"])  # <-- novo
    cred = credentials.Certificate(firebase_config)                    # <-- novo
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    print(f"ERRO CRÍTICO AO INICIALIZAR O FIREBASE: {e}")
    db = None


# --- CONFIGURAÇÃO DO APP FLASK ---
app = Flask(__name__)
app.secret_key = 'Xablau'
FIREBASE_WEB_API_KEY = "23abc220ab03cc00bb9c271e983b50dc96672311"


# --- FILTRO DE TEMPLATE PARA WHATSAPP ---
@app.template_filter('format_whatsapp')
def format_whatsapp(phone_number):
    """Remove todos os caracteres não numéricos e adiciona o código do Brasil (55)."""
    if not phone_number:
        return ""
    cleaned_number = re.sub(r'[()\-\s]', '', phone_number)
    if not cleaned_number.startswith('55'):
        return f'55{cleaned_number}'
    return cleaned_number


# ================================================================
# --- SEÇÃO 1: AUTENTICAÇÃO E CONTROLO DE ACESSO ---
# ================================================================

def login_and_db_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if db is None:
            flash("Erro crítico de conexão com o banco de dados. Verifique as credenciais do servidor.", "error")
            return redirect(url_for('login'))
        if 'user' not in session:
            flash("Você precisa fazer login para acessar esta página.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/login", methods=["GET", "POST"])
def login():
    if 'user' in session:
        return redirect(url_for('dashboard'))

    if request.method == "POST":
        email = request.form.get('email')
        password = request.form.get('password')
        rest_api_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
        payload = json.dumps({
            "email": email,
            "password": password,
            "returnSecureToken": True
        })
        headers = {"Content-Type": "application/json"}

        try:
            r = requests.post(rest_api_url, data=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            if 'idToken' in data:
                session['user'] = data['email']
                session['idToken'] = data['idToken']
                return redirect(url_for('dashboard'))
        except requests.exceptions.HTTPError:
            print("Erro Firebase:", r.text)  # Mostra o erro real no console
            flash("Credenciais inválidas. Verifique o seu e-mail e senha.", "error")
        except Exception as e:
            flash(f"Ocorreu um erro de conexão: {e}", "error")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop('user', None)
    session.pop('idToken', None)
    flash("Você saiu da sua conta.", "success")
    return redirect(url_for('login'))

# Exemplo de rota protegida
@app.route("/dashboard")
@login_and_db_required
def dashboard():
    return f"Bem-vindo, {session['user']}!"


# ================================================================
# --- SEÇÃO 2: PÁGINAS PRINCIPAIS (DASHBOARD, CONFIGURAÇÕES) ---
# ================================================================

@app.route("/")
@login_and_db_required
def dashboard():
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    valor_concluido_mes, count_concluido_mes, valor_concluido_total, count_aberto_total = 0, 0, 0, 0

    docs_gerais = db.collection('ordens_servico').stream()
    for doc in docs_gerais:
        os_data = doc.to_dict()
        status = os_data.get('status', 'Aberta')
        
        if status == 'Concluída':
            valor_os = os_data.get('orcamento_servicos', 0) + os_data.get('orcamento_produtos', 0)
            valor_concluido_total += valor_os
            
            data_abertura = os_data.get('data_abertura')
            if data_abertura and data_abertura >= start_of_month:
                valor_concluido_mes += valor_os
                count_concluido_mes += 1
        elif status != 'Cancelada':
            count_aberto_total += 1

    recentes_ref = db.collection('ordens_servico').order_by('data_abertura', direction=firestore.Query.DESCENDING).limit(5).stream()
    os_recentes = [doc.to_dict() | {'id': doc.id, 'data_abertura_fmt': doc.to_dict().get('data_abertura', datetime.now()).strftime("%d/%m/%Y")} for doc in recentes_ref]
        
    stats = {
        'valor_concluido_mes': valor_concluido_mes, 'count_concluido_mes': count_concluido_mes,
        'valor_concluido_total': valor_concluido_total, 'count_aberto_total': count_aberto_total
    }
    return render_template("dashboard.html", active_page='dashboard', stats=stats, ordens_servico=os_recentes)

@app.route("/configuracoes", methods=["GET", "POST"])
@login_and_db_required
def configuracoes_page():
    settings_ref = db.collection('configuracoes').document('empresa')
    if request.method == "POST":
        form_name = request.form.get('form_name')
        if form_name == 'change_password':
            new_password, confirm_password = request.form.get('new_password'), request.form.get('confirm_password')
            if not new_password or new_password != confirm_password:
                flash("As senhas não coincidem ou estão vazias.", "error")
            else:
                rest_api_url = f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={FIREBASE_WEB_API_KEY}"
                payload = json.dumps({"idToken": session.get('idToken'), "password": new_password, "returnSecureToken": True})
                try:
                    r = requests.post(rest_api_url, data=payload)
                    r.raise_for_status()
                    session['idToken'] = r.json().get('idToken')
                    flash("Senha alterada com sucesso!", "success")
                except Exception:
                    flash("A sua sessão pode ter expirado. Faça login novamente para alterar a senha.", "error")
        elif form_name == 'company_info':
            company_data = {'cnpj': request.form.get('cnpj'), 'endereco': request.form.get('endereco'), 'nome_empresa': request.form.get('nome_empresa')}
            settings_ref.set(company_data)
            flash("Informações da empresa salvas com sucesso!", "success")
        return redirect(url_for('configuracoes_page'))
    
    current_settings = (settings_ref.get()).to_dict() if settings_ref.get().exists else {}
    return render_template("configuracoes.html", active_page='configuracoes', settings=current_settings)


# ================================================================
# --- SEÇÃO 3: ORDEM DE SERVIÇO (OS) ---
# ================================================================

@app.route("/ordens_servico")
@login_and_db_required
def ordens_servico_page():
    filtro_cliente = request.args.get('filtro_cliente', '').strip()
    filtro_status = request.args.get('filtro_status', '').strip()
    filtros_ativos = {'cliente': filtro_cliente, 'status': filtro_status}
    
    try:
        query = db.collection('ordens_servico')
        if filtro_cliente: query = query.where('cliente_nome', '==', filtro_cliente)
        if filtro_status: query = query.where('status', '==', filtro_status)
        
        if not filtro_cliente and not filtro_status:
            os_ref = query.order_by('data_abertura', direction=firestore.Query.DESCENDING).stream()
        else:
            os_ref = query.stream()

        lista_os, total_concluido, count_concluido, total_aberto, count_aberto, total_cancelado, count_cancelado = [], 0, 0, 0, 0, 0, 0

        for os_doc in os_ref:
            os_data = os_doc.to_dict()
            os_data['id'] = os_doc.id
            os_data['data_abertura_fmt'] = os_data.get('data_abertura', datetime.now()).strftime("%d/%m/%Y")
            lista_os.append(os_data)
            
            status = os_data.get('status', '')
            valor_os = os_data.get('orcamento_servicos', 0) + os_data.get('orcamento_produtos', 0)

            if status == 'Concluída':
                total_concluido += valor_os; count_concluido += 1
            elif status == 'Cancelada':
                total_cancelado += valor_os; count_cancelado += 1
            else:
                total_aberto += valor_os; count_aberto += 1
    except Exception as e:
        flash(f"Erro ao carregar dados: {e}", "error")
        lista_os, total_concluido, count_concluido, total_aberto, count_aberto, total_cancelado, count_cancelado = [], 0, 0, 0, 0, 0, 0

    relatorio = {
        'total_concluido': total_concluido, 'count_concluido': count_concluido,
        'total_aberto': total_aberto, 'count_aberto': count_aberto,
        'total_cancelado': total_cancelado, 'count_cancelado': count_cancelado
    }
    
    return render_template("ordens_servico.html", active_page='ordens_servico', lista_os=lista_os, relatorio=relatorio, filtros=filtros_ativos)

@app.route("/gerar_os", methods=["GET", "POST"])
@login_and_db_required
def gerar_os_page():
    if request.method == "POST":
        dados_os = {
            "cliente_nome": request.form.get('cliente_nome'), "cliente_doc": request.form.get('cliente_doc'),
            "cliente_endereco": request.form.get('cliente_endereco'), "cliente_cidade": request.form.get('cliente_cidade'),
            "cliente_telefone": request.form.get('cliente_telefone'), "cliente_email": request.form.get('cliente_email'),
            "servico_titulo": request.form.get('servico_titulo'), "servico_detalhes": request.form.get('servico_detalhes'),
            "servico_escopo": request.form.get('servico_escopo'), "servico_garantia": request.form.get('servico_garantia'),
            "orcamento_servicos": float(request.form.get('orcamento_servicos', 0)),
            "orcamento_produtos": float(request.form.get('orcamento_produtos', 0)),
            "orcamento_horas": request.form.get('orcamento_horas'),
            "orcamento_valor_hora": request.form.get('orcamento_valor_hora'),
            "data_abertura": firestore.SERVER_TIMESTAMP, "status": "Aberta"
        }
        db.collection('ordens_servico').add(dados_os)
        flash("Ordem de Serviço gerada com sucesso!", "success")
        return redirect(url_for('ordens_servico_page'))

    clientes_ref = db.collection('clientes').order_by('nome').stream()
    lista_clientes = [doc.to_dict() | {'id': doc.id} for doc in clientes_ref]
    clientes_json = json.dumps(lista_clientes)
    dados_calculadora = {"titulo": request.args.get('titulo'), "escopo": request.args.get('escopo'), "valor": request.args.get('valor'), "horas": request.args.get('horas'), "valor_hora": request.args.get('valor_hora')}
    
    return render_template("gerar_os.html", active_page='gerar_os', clientes=lista_clientes, clientes_json=clientes_json, dados_calculadora=dados_calculadora)

@app.route("/gerenciar_os/<os_id>")
@login_and_db_required
def gerenciar_os_page(os_id):
    os_doc_ref = db.collection('ordens_servico').document(os_id)
    os_doc = os_doc_ref.get()

    if not os_doc.exists: return "Ordem de Serviço não encontrada!", 404
    
    dados_os = os_doc.to_dict()
    dados_os['id'] = os_doc.id
    if dados_os.get('data_abertura'): dados_os['data_abertura_fmt'] = dados_os['data_abertura'].strftime("%d/%m/%Y")
    
    pagamentos_ref = os_doc_ref.collection('pagamentos').order_by("data", direction=firestore.Query.DESCENDING).stream()
    historico_pagamentos, total_pago = [], 0
    for pag in pagamentos_ref:
        pag_data = pag.to_dict()
        total_pago += pag_data.get('valor', 0)
        if pag_data.get('data'): pag_data['data_fmt'] = pag_data['data'].strftime("%d/%m/%Y")
        historico_pagamentos.append(pag_data)

    valor_total_os = dados_os.get('orcamento_servicos', 0) + dados_os.get('orcamento_produtos', 0)
    saldo_devedor = valor_total_os - total_pago

    if total_pago <= 0: status_pagamento = "Aguardando Pagamento"
    elif saldo_devedor > 0: status_pagamento = "Pago Parcialmente"
    else: status_pagamento = "Pago Integralmente"

    financeiro = {"valor_total": valor_total_os, "total_pago": total_pago, "saldo_devedor": saldo_devedor, "status": status_pagamento, "historico": historico_pagamentos}
    
    comentarios_ref = os_doc_ref.collection('comentarios').order_by('data_registro', direction=firestore.Query.DESCENDING).stream()
    historico_comentarios = []
    for comentario in comentarios_ref:
        com_data = comentario.to_dict()
        if com_data.get('data_registro'): com_data['data_fmt'] = com_data['data_registro'].strftime('%d/%m/%Y às %H:%M')
        historico_comentarios.append(com_data)
    
    settings_doc = db.collection('configuracoes').document('empresa').get()
    empresa_info = settings_doc.to_dict() if settings_doc.exists else {}

    return render_template("gerenciar_os.html", os=dados_os, empresa=empresa_info, financeiro=financeiro, comentarios=historico_comentarios)

@app.route("/os_cliente/<os_id>")
@login_and_db_required
def os_cliente_page(os_id):
    os_doc = db.collection('ordens_servico').document(os_id).get()
    if not os_doc.exists: return "Ordem de Serviço não encontrada!", 404
    
    dados_os = os_doc.to_dict()
    dados_os['id'] = os_doc.id
    if dados_os.get('data_abertura'): dados_os['data_abertura_fmt'] = dados_os['data_abertura'].strftime("%d/%m/%Y")

    settings_doc = db.collection('configuracoes').document('empresa').get()
    empresa_info = settings_doc.to_dict() if settings_doc.exists else {}
    return render_template("os_cliente.html", os=dados_os, empresa=empresa_info)

@app.route("/adicionar_pagamento/<os_id>", methods=["POST"])
@login_and_db_required
def adicionar_pagamento(os_id):
    try:
        valor_pago = float(request.form.get("valor_pago"))
        metodo = request.form.get("metodo_pagamento")
        data_pagamento_str = request.form.get("data_pagamento")
        
        data_pagamento = datetime.strptime(data_pagamento_str, "%Y-%m-%d")

        dados_pagamento = {"valor": valor_pago, "metodo": metodo, "data": data_pagamento, "data_registro": firestore.SERVER_TIMESTAMP}
        db.collection('ordens_servico').document(os_id).collection('pagamentos').add(dados_pagamento)
        flash("Pagamento registado com sucesso!", "success")
    except Exception as e:
        flash(f"Ocorreu um erro ao registar o pagamento: {e}", "error")
    
    return redirect(url_for('gerenciar_os_page', os_id=os_id))

@app.route("/adicionar_comentario/<os_id>", methods=["POST"])
@login_and_db_required
def adicionar_comentario(os_id):
    try:
        texto_comentario = request.form.get("texto_comentario")
        if not texto_comentario:
            flash("O comentário não pode estar vazio.", "error")
        else:
            dados_comentario = {"texto": texto_comentario, "autor": session.get('user', 'Sistema'), "data_registro": firestore.SERVER_TIMESTAMP}
            db.collection('ordens_servico').document(os_id).collection('comentarios').add(dados_comentario)
            flash("Comentário adicionado com sucesso.", "success")
    except Exception as e:
        flash(f"Ocorreu um erro ao adicionar o comentário: {e}", "error")
    return redirect(url_for('gerenciar_os_page', os_id=os_id))

@app.route("/atualizar_status_os/<os_id>", methods=["POST"])
@login_and_db_required
def atualizar_status_os(os_id):
    novo_status = request.form.get('novo_status')
    if novo_status:
        try:
            db.collection('ordens_servico').document(os_id).update({"status": novo_status})
            flash(f"Status da O.S. atualizado para '{novo_status}'.", "success")
        except Exception as e:
            flash(f"Erro ao atualizar status: {e}", "error")
    return redirect(url_for('ordens_servico_page'))


# ================================================================
# --- SEÇÃO 4: CARTEIRA DE CLIENTES ---
# ================================================================

@app.route("/clientes")
@login_and_db_required
def clientes_page():
    clientes_ref = db.collection('clientes').order_by('nome').stream()
    lista_clientes = [doc.to_dict() | {'id': doc.id} for doc in clientes_ref]
    return render_template("clientes.html", active_page='clientes', clientes=lista_clientes)

@app.route("/adicionar_cliente", methods=["POST"])
@login_and_db_required
def adicionar_cliente():
    try:
        dados_cliente = {"nome": request.form.get('nome'), "doc": request.form.get('doc'), "endereco": request.form.get('endereco'), "cidade": request.form.get('cidade'), "telefone": request.form.get('telefone'), "email": request.form.get('email')}
        db.collection('clientes').add(dados_cliente)
        flash("Cliente adicionado com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao adicionar cliente: {e}", "error")
    return redirect(url_for('clientes_page'))

@app.route("/editar_cliente/<cliente_id>", methods=["POST"])
@login_and_db_required
def editar_cliente(cliente_id):
    try:
        dados_atualizados = {"nome": request.form.get('nome'), "doc": request.form.get('doc'), "endereco": request.form.get('endereco'), "cidade": request.form.get('cidade'), "telefone": request.form.get('telefone'), "email": request.form.get('email')}
        db.collection('clientes').document(cliente_id).update(dados_atualizados)
        flash("Cliente atualizado com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao atualizar cliente: {e}", "error")
    return redirect(url_for('clientes_page'))

@app.route("/excluir_cliente/<cliente_id>", methods=["POST"])
@login_and_db_required
def excluir_cliente(cliente_id):
    try:
        db.collection('clientes').document(cliente_id).delete()
        flash("Cliente excluído com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao excluir cliente: {e}", "error")
    return redirect(url_for('clientes_page'))


# ================================================================
# --- SEÇÃO 5: FERRAMENTAS (CALCULADORA, AGENDA) ---
# ================================================================

@app.route("/calculadora")
@login_and_db_required
def calculadora_page():
    return render_template("calculadora.html", active_page='calculadora')

@app.route("/agenda", methods=["GET", "POST"])
@login_and_db_required
def agenda_page():
    if request.method == "POST":
        try:
            data_str, hora_str = request.form.get("data_evento"), request.form.get("hora_evento")
            data_evento_completa = datetime.strptime(f"{data_str} {hora_str}", "%Y-%m-%d %H:%M")
            
            cliente_id, cliente_nome = request.form.get("cliente_id"), ""
            if cliente_id:
                cliente_doc = db.collection('clientes').document(cliente_id).get()
                if cliente_doc.exists:
                    cliente_nome = cliente_doc.to_dict().get('nome')

            dados_agendamento = {
                "titulo": request.form.get("titulo"), "data_evento": data_evento_completa,
                "cliente_id": cliente_id, "cliente_nome": cliente_nome,
                "descricao": request.form.get("descricao"), "status": "Agendado"
            }
            db.collection('agenda').add(dados_agendamento)
            flash("Agendamento criado com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao criar agendamento: {e}", "error")
        return redirect(url_for('agenda_page'))

    now = datetime.now(timezone.utc)
    agenda_ref = db.collection('agenda').where('data_evento', '>=', now).order_by('data_evento').stream()
    
    lista_agendamentos = []
    for agendamento in agenda_ref:
        dados = agendamento.to_dict()
        dados['id'] = agendamento.id
        if dados.get('data_evento'):
            dados['data_fmt'] = dados['data_evento'].strftime("%d/%m/%Y")
            dados['hora_fmt'] = dados['data_evento'].strftime("%H:%M")
        lista_agendamentos.append(dados)

    clientes_ref = db.collection('clientes').order_by('nome').stream()
    lista_clientes = [doc.to_dict() | {'id': doc.id} for doc in clientes_ref]

    return render_template("agenda.html", active_page='agenda', agendamentos=lista_agendamentos, clientes=lista_clientes)


@app.route("/excluir_agendamento/<agendamento_id>", methods=["POST"])
@login_and_db_required
def excluir_agendamento(agendamento_id):
    try:
        db.collection('agenda').document(agendamento_id).delete()
        flash("Agendamento excluído com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao excluir agendamento: {e}", "error")
    return redirect(url_for('agenda_page'))

# ================================================================
# --- SEÇÃO 6: EXPORTAÇÃO DE DADOS ---
# ================================================================

@app.route("/exportar_csv")
@login_and_db_required
def exportar_csv():
    filtro_cliente = request.args.get('filtro_cliente', '').strip()
    filtro_status = request.args.get('filtro_status', '').strip()

    try:
        query = db.collection('ordens_servico')
        if filtro_cliente: query = query.where('cliente_nome', '==', filtro_cliente)
        if filtro_status: query = query.where('status', '==', filtro_status)
        os_ref = query.order_by('data_abertura', direction=firestore.Query.DESCENDING).stream()

        def generate():
            data_buffer = io.StringIO()
            writer = csv.writer(data_buffer)
            writer.writerow(["ID da OS", "Data de Abertura", "Cliente", "Servico", "Valor Servicos (R$)", "Valor Produtos (R$)", "Valor Total (R$)", "Status"])
            yield data_buffer.getvalue(); data_buffer.seek(0); data_buffer.truncate(0)

            for os_doc in os_ref:
                os_data = os_doc.to_dict()
                valor_total = os_data.get('orcamento_servicos', 0) + os_data.get('orcamento_produtos', 0)
                writer.writerow([
                    os_doc.id, os_data.get('data_abertura', '').strftime("%Y-%m-%d") if os_data.get('data_abertura') else '',
                    os_data.get('cliente_nome', ''), os_data.get('servico_titulo', ''),
                    f"{os_data.get('orcamento_servicos', 0):.2f}", f"{os_data.get('orcamento_produtos', 0):.2f}",
                    f"{valor_total:.2f}", os_data.get('status', '')
                ])
                yield data_buffer.getvalue(); data_buffer.seek(0); data_buffer.truncate(0)

        response = Response(generate(), mimetype='text/csv')
        response.headers.set("Content-Disposition", "attachment", filename="relatorio_os.csv")
        return response
    except Exception as e:
        flash(f"Erro ao exportar dados: {e}", "error")
        return redirect(url_for('ordens_servico_page'))


# --- INICIALIZAÇÃO DO SERVIDOR ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
