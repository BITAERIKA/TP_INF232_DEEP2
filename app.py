from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from io import BytesIO
import base64
import folium
from datetime import datetime
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///signalements.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
db = SQLAlchemy(app)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --------- Modèles ---------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom_complet = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')  # user, agent, admin

class Signalement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    email_anonyme = db.Column(db.String(120), nullable=True)
    quartier = db.Column(db.String(100), nullable=False)
    type_insalubrite = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    image_filename = db.Column(db.String(200), nullable=True)
    statut = db.Column(db.String(20), nullable=False, default='soumis')  # soumis, en_cours, traite
    date_signalement = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('signalements', lazy=True))

# --------- Coordonnées et types ---------
QUARTIERS = {
    "Bastos": (3.8796, 11.5125), "Mendong": (3.8373, 11.4803), "Mvog Mbi": (3.8410, 11.5190),
    "Nlongkak": (3.8780, 11.5170), "Etoudi": (3.8890, 11.5080), "Mvolyé": (3.8250, 11.5000),
    "Melen": (3.8510, 11.4980), "Tsinga": (3.8670, 11.5100), "Ekounou": (3.8310, 11.5380),
    "Ngousso": (3.8950, 11.5400), "Olembe": (3.9200, 11.5100), "Mimboman": (3.8450, 11.5500),
    "Nkolbisson": (3.8700, 11.4500), "Simbock": (3.8200, 11.5300), "Cité Verte": (3.8750, 11.5350),
    "Biyem-Assi": (3.8430, 11.4860), "Nsam": (3.8280, 11.5290), "Essos": (3.8800, 11.5330),
    "Mvog Ada": (3.8630, 11.5200)
}
TYPES = ["Dépôt d'ordures sauvage", "Canalisation bouchée", "Eaux stagnantes", "Déchets toxiques", "Autre"]
STATUTS = ['soumis', 'en_cours', 'traite']

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Décorateurs
def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                flash("Veuillez vous connecter.", "warning")
                return redirect(url_for('login'))
            user = User.query.get(session['user_id'])
            if user.role != role and user.role != 'admin':
                flash("Accès réservé.", "danger")
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash("Veuillez vous connecter.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

with app.app_context():
    db.create_all()
    if not User.query.filter_by(email='agent@yaounde.com').first():
        agent = User(
            nom_complet='Agent Municipal',
            email='agent@yaounde.com',
            password=generate_password_hash('agent123'),
            role='agent'
        )
        db.session.add(agent)
        db.session.commit()

# --------- Routes publiques ---------
@app.route('/')
def index():
    total = Signalement.query.count()
    traites = Signalement.query.filter_by(statut='traite').count()
    en_cours = Signalement.query.filter_by(statut='en_cours').count()
    derniers = Signalement.query.order_by(Signalement.id.desc()).limit(6).all()
    user_role = None
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        user_role = user.role
    return render_template('index.html', total=total, traites=traites, en_cours=en_cours, derniers=derniers, user_role=user_role)

@app.route('/signalements')
def liste_signalements():
    page = request.args.get('page', 1, type=int)
    signalements = Signalement.query.order_by(Signalement.id.desc()).paginate(page=page, per_page=10, error_out=False)
    return render_template('liste.html', signalements=signalements)

@app.route('/signalement/<int:id>')
def detail_signalement(id):
    signalement = Signalement.query.get_or_404(id)
    return render_template('detail.html', signalement=signalement)

@app.route('/signaler-anonyme', methods=['GET', 'POST'])
def signaler_anonyme():
    if request.method == 'POST':
        quartier = request.form['quartier'].strip()
        type_insal = request.form['type_insalubrite']
        description = request.form.get('description', '').strip()
        email = request.form.get('email', '').strip()
        date_str = request.form['date_signalement']

        if quartier not in QUARTIERS or type_insal not in TYPES:
            flash("Données invalides.", "danger")
            return redirect(url_for('signaler_anonyme'))
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        if date_obj > datetime.today().date():
            flash("Date future impossible.", "danger")
            return redirect(url_for('signaler_anonyme'))

        file = request.files.get('image')
        filename = None
        if file and file.filename != '' and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            name, ext = os.path.splitext(filename)
            filename = f"{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        signalement = Signalement(
            user_id=session.get('user_id'),      # <-- attribue au compte si connecté
            email_anonyme=email if email else None,
            quartier=quartier,
            type_insalubrite=type_insal,
            description=description,
            image_filename=filename,
            date_signalement=date_obj
        )
        db.session.add(signalement)
        db.session.commit()
        flash("Signalement enregistré avec succès !", "success")
        return redirect(url_for('liste_signalements'))
    return render_template('signaler.html', quartiers=sorted(QUARTIERS.keys()), types=TYPES, today=datetime.today().strftime('%Y-%m-%d'))

# --------- Authentification ---------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nom = request.form['nom_complet'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        confirm = request.form['confirm_password']
        if not nom or not email or not password:
            flash("Tous les champs sont obligatoires.", "danger")
            return redirect(url_for('register'))
        if password != confirm:
            flash("Mots de passe différents.", "danger")
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash("Email déjà utilisé.", "warning")
            return redirect(url_for('register'))
        user = User(nom_complet=nom, email=email, password=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        session['user_name'] = user.nom_complet
        flash("Compte créé, vous êtes connecté.", "success")
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['user_name'] = user.nom_complet
            flash(f"Bienvenue, {user.nom_complet} !", "success")
            return redirect(url_for('index'))
        else:
            flash("Identifiants incorrects.", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Déconnexion réussie.", "info")
    return redirect(url_for('index'))

# --------- Utilisateur connecté : mes signalements ---------
@app.route('/mes-signalements')
@login_required
def mes_signalements():
    user = User.query.get(session['user_id'])
    signalements = Signalement.query.filter_by(user_id=user.id).order_by(Signalement.id.desc()).all()
    return render_template('mes_signalements.html', signalements=signalements)

# --------- Espace Agent ---------
@app.route('/agent')
@role_required('agent')
def agent_dashboard():
    signalements = Signalement.query.order_by(Signalement.date_signalement.desc()).all()
    return render_template('agent_dashboard.html', signalements=signalements, statuts=STATUTS)

@app.route('/changer_statut/<int:id>', methods=['POST'])
@role_required('agent')
def changer_statut(id):
    signalement = Signalement.query.get_or_404(id)
    nouveau = request.form.get('statut')
    if nouveau in STATUTS:
        signalement.statut = nouveau
        db.session.commit()
        flash(f"Statut du signalement #{id} mis à jour : {nouveau}.", "success")
    else:
        flash("Statut invalide.", "danger")
    return redirect(url_for('agent_dashboard'))

# --------- Analyses avancées (avec carte) ---------
@app.route('/analyse')
def analyse():
    signalements = Signalement.query.all()
    if not signalements:
        flash("Aucun signalement.", "info")
        return redirect(url_for('index'))
    data = [{
        'Quartier': s.quartier,
        'Type': s.type_insalubrite,
        'Statut': s.statut,
        'Date': s.date_signalement
    } for s in signalements]
    df = pd.DataFrame(data)
    nb_total = len(df)
    top_quartiers = df['Quartier'].value_counts().head(10).reset_index()
    top_quartiers.columns = ['Quartier', 'Nombre']
    # Moyenne signalements par jour
    df['Date'] = pd.to_datetime(df['Date'])
    min_date = df['Date'].min()
    max_date = df['Date'].max()
    jours = (max_date - min_date).days + 1
    moyenne_par_jour = round(nb_total / jours, 2) if jours > 0 else nb_total
    # Mode du type
    mode_type = df['Type'].mode().iloc[0] if not df['Type'].mode().empty else 'N/A'
    # Graphiques
    # 1. Barres par quartier
    fig1, ax1 = plt.subplots(figsize=(10, 6))
    sns.countplot(y='Quartier', data=df, order=df['Quartier'].value_counts().index, ax=ax1, palette='Greens_r')
    ax1.set_title('Signalements par quartier')
    buf1 = BytesIO(); plt.tight_layout(); plt.savefig(buf1, format='png'); plt.close()
    bar_img = base64.b64encode(buf1.getvalue()).decode('utf-8')
    # 2. Camembert type
    fig2, ax2 = plt.subplots()
    df['Type'].value_counts().plot.pie(autopct='%1.1f%%', ax=ax2, colors=['#FFC107','#2196F3','#4CAF50','#F44336','#9C27B0'])
    ax2.set_title('Types d\'insalubrité')
    buf2 = BytesIO(); plt.tight_layout(); plt.savefig(buf2, format='png'); plt.close()
    pie_img = base64.b64encode(buf2.getvalue()).decode('utf-8')
    # 3. Évolution temporelle (par mois)
    df['mois_annee'] = df['Date'].dt.to_period('M')
    ts = df['mois_annee'].value_counts().sort_index()
    fig3, ax3 = plt.subplots(figsize=(10, 5))
    ts.plot(kind='bar', ax=ax3, color='green')
    ax3.set_title('Évolution mensuelle des signalements')
    ax3.set_xlabel('Mois')
    ax3.set_ylabel('Nombre')
    buf3 = BytesIO(); plt.tight_layout(); plt.savefig(buf3, format='png'); plt.close()
    time_img = base64.b64encode(buf3.getvalue()).decode('utf-8')
    # Carte intégrée
    carte_iframe = url_for('carte')
    return render_template('analyse.html',
                           nb_total=nb_total,
                           top_quartiers=top_quartiers,
                           moyenne_par_jour=moyenne_par_jour,
                           mode_type=mode_type,
                           bar_img=bar_img,
                           pie_img=pie_img,
                           time_img=time_img,
                           carte_iframe=carte_iframe)

@app.route('/carte')
def carte():
    signalements = Signalement.query.all()
    if not signalements:
        return "<p>Aucune donnée</p>"
    m = folium.Map(location=[3.848, 11.502], zoom_start=12)
    for s in signalements:
        coord = QUARTIERS.get(s.quartier)
        if coord:
            couleur = {'soumis': 'orange', 'en_cours': 'blue', 'traite': 'green'}[s.statut]
            folium.CircleMarker(
                location=coord,
                radius=8,
                color=couleur,
                fill=True,
                fill_opacity=0.7,
                popup=f"<b>{s.quartier}</b><br>{s.type_insalubrite}<br>Statut: {s.statut}"
            ).add_to(m)
    return m._repr_html_()

if __name__ == '__main__':
    app.run(debug=True)