import os
import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from contextlib import contextmanager
from datetime import datetime

import pandas as pd
import streamlit as st

# -------------- Config ----------------
st.set_page_config(page_title="Mini-Brevo (Demo)", page_icon="📧", layout="wide")

DB_PATH = os.path.join(os.path.dirname(__file__), "mini_brevo.db")

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER or "demo@example.com")
FROM_NAME = os.getenv("FROM_NAME", "Mini-Brevo Demo")

# -------------- DB Helpers ------------
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                email TEXT UNIQUE,
                tags TEXT,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                body TEXT,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER,
                contact_id INTEGER,
                email TEXT,
                status TEXT,
                error TEXT,
                sent_at TEXT
            )
        """)
        conn.commit()

@st.cache_data
def load_contacts_df():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM contacts ORDER BY id DESC", conn)

def refresh_contacts():
    st.session_state["contacts_df"] = load_contacts_df()

@st.cache_data
def load_campaigns_df():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM campaigns ORDER BY id DESC", conn)

def refresh_campaigns():
    st.session_state["campaigns_df"] = load_campaigns_df()

def insert_contact(name, email, tags):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO contacts(name, email, tags, created_at) VALUES (?,?,?,?)",
            (name.strip(), email.strip().lower(), tags.strip(), datetime.utcnow().isoformat())
        )
        conn.commit()

def bulk_import_contacts(df: pd.DataFrame):
    count = 0
    for _, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        email = str(row.get("email", "")).strip().lower()
        tags = str(row.get("tags", "")).strip()
        if email:
            try:
                insert_contact(name, email, tags)
                count += 1
            except Exception:
                pass
    refresh_contacts()
    return count

def insert_campaign(subject, body):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO campaigns(subject, body, created_at) VALUES (?,?,?)",
            (subject.strip(), body, datetime.utcnow().isoformat())
        )
        conn.commit()

def log_send(campaign_id, contact_id, email, status, error=""):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sends(campaign_id, contact_id, email, status, error, sent_at) VALUES (?,?,?,?,?,?)",
            (campaign_id, contact_id, email, status, error, datetime.utcnow().isoformat())
        )
        conn.commit()

@st.cache_data
def load_sends_df(limit=500):
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT s.id, s.sent_at, s.status, s.error, s.email, c.subject "
            "FROM sends s LEFT JOIN campaigns c ON s.campaign_id = c.id "
            "ORDER BY s.id DESC LIMIT ?", conn, params=(limit,)
        )

def refresh_sends():
    st.session_state["sends_df"] = load_sends_df()

# -------------- Email Sender ----------
def send_email_smtp(to_email: str, subject: str, body_html: str):
    """
    Sends an email via SMTP if SMTP_* env vars are set.
    Returns (ok: bool, error: str)
    """
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and FROM_EMAIL):
        # Simulation mode
        return True, "SIMULATED"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"] = to_email

        part_html = MIMEText(body_html, "html", "utf-8")
        msg.attach(part_html)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return True, ""
    except Exception as e:
        return False, str(e)

# -------------- UI --------------------
def page_contacts():
    st.header("👥 Contactos")
    with st.expander("➕ Agregar contacto individual"):
        with st.form("add_contact_form"):
            col1, col2, col3 = st.columns([1,1,1])
            with col1:
                name = st.text_input("Nombre")
            with col2:
                email = st.text_input("Email *")
            with col3:
                tags = st.text_input("Etiquetas (opcional, separadas por coma)")
            submitted = st.form_submit_button("Guardar")
            if submitted:
                if not email:
                    st.error("El email es obligatorio.")
                else:
                    insert_contact(name, email, tags)
                    st.success(f"Contacto '{email}' guardado (o ya existía).")
                    refresh_contacts()

    st.divider()
    st.subheader("📤 Importar desde CSV")
    st.caption("El CSV debe tener columnas: email, name (opcional), tags (opcional).")
    file = st.file_uploader("Subir CSV", type=["csv"])
    if file:
        df = pd.read_csv(file)
        st.dataframe(df.head(20))
        if st.button("Importar contactos"):
            count = bulk_import_contacts(df)
            st.success(f"Importados {count} contactos.")
    st.divider()

    df_contacts = st.session_state.get("contacts_df", load_contacts_df())
    st.subheader(f"📋 Listado de contactos ({len(df_contacts)})")
    st.dataframe(df_contacts, use_container_width=True)

def page_campaigns():
    st.header("📝 Campañas (borradores)")
    with st.form("new_campaign_form"):
        subject = st.text_input("Asunto *")
        body = st.text_area("Contenido (HTML o texto) *", height=200,
                            value="<h2>Hola {{name}}</h2><p>Este es un mensaje de prueba.</p>")
        submitted = st.form_submit_button("Crear campaña")
        if submitted:
            if subject and body:
                insert_campaign(subject, body)
                st.success("Campaña creada.")
                refresh_campaigns()
            else:
                st.error("Asunto y contenido son obligatorios.")

    st.divider()
    df_campaigns = st.session_state.get("campaigns_df", load_campaigns_df())
    st.subheader(f"📚 Borradores ({len(df_campaigns)})")
    st.dataframe(df_campaigns, use_container_width=True)

def page_send():
    st.header("🚀 Enviar campaña")
    df_campaigns = st.session_state.get("campaigns_df", load_campaigns_df())
    df_contacts = st.session_state.get("contacts_df", load_contacts_df())

    if df_campaigns.empty or df_contacts.empty:
        st.warning("Necesitas al menos 1 campaña y 1 contacto.")
        return

    campaign = st.selectbox("Selecciona campaña", df_campaigns.itertuples(), format_func=lambda r: f"[{r.id}] {r.subject}")
    recipients = st.multiselect(
        "Destinatarios",
        options=df_contacts.itertuples(),
        default=list(df_contacts.itertuples()),
        format_func=lambda r: f"{r.email} ({r.name})" if r.name else r.email
    )

    test_email = st.text_input("Enviar prueba a (opcional)")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Enviar PRUEBA"):
            if test_email:
                # Personalize simple
                body = campaign.body.replace("{{name}}", "Prueba")
                ok, err = send_email_smtp(test_email, campaign.subject, body)
                status = "SENT" if ok else "ERROR"
                log_send(campaign.id, None, test_email, status, err)
                refresh_sends()
                if ok:
                    st.success("Prueba enviada (o simulada si no configuraste SMTP).")
                else:
                    st.error(f"Error enviando: {err}")
            else:
                st.info("Escribe un email para la prueba.")
    with col2:
        if st.button("Enviar a TODOS los seleccionados"):
            sent_ok = 0
            for r in recipients:
                personalized = campaign.body.replace("{{name}}", r.name or "")
                ok, err = send_email_smtp(r.email, campaign.subject, personalized)
                status = "SENT" if ok else "ERROR"
                log_send(campaign.id, r.id, r.email, status, err)
                if ok: sent_ok += 1
            refresh_sends()
            st.success(f"Proceso finalizado. Enviados OK: {sent_ok}/{len(recipients)} (o simulados).")

def page_logs():
    st.header("📈 Historial de envíos")
    df = st.session_state.get("sends_df", load_sends_df())
    st.dataframe(df, use_container_width=True)
    st.caption("Nota: En modo demo, si no configuras variables SMTP, los envíos se simulan y se registran como 'SENT (SIMULATED)'.")

def main():
    init_db()
    if "contacts_df" not in st.session_state:
        refresh_contacts()
    if "campaigns_df" not in st.session_state:
        refresh_campaigns()
    if "sends_df" not in st.session_state:
        refresh_sends()

    st.sidebar.title("Mini-Brevo")
    page = st.sidebar.radio("Menú", ["Contactos", "Campañas", "Enviar", "Logs"])
    if page == "Contactos":
        page_contacts()
    elif page == "Campañas":
        page_campaigns()
    elif page == "Enviar":
        page_send()
    else:
        page_logs()

    with st.sidebar.expander("ℹ️ Ayuda"):
        st.markdown("""
**Mini-Brevo (Demo)**  
- Agrega contactos, crea campañas y envía (o simula) correos.  
- Personalización básica: usa `{{name}}` en el cuerpo para reemplazar el nombre.  
- Para envíos reales, configura variables de entorno SMTP.
        """)

if __name__ == "__main__":
    main()
