# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import os
import io
import uuid
import datetime
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import base64
from pymongo import MongoClient

# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(16))

# Configurar MongoDB
mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
client = MongoClient(mongo_uri)
db = client.image_classification_db

# Configurar Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Modelo de usuario
class User(UserMixin):
    def __init__(self, id, username, password_hash, full_name, role):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.full_name = full_name
        self.role = role

# Cargar usuario desde la base de datos
@login_manager.user_loader
def load_user(user_id):
    user_data = db.users.find_one({"_id": user_id})
    if not user_data:
        return None
    return User(
        id=user_data['_id'],
        username=user_data['username'],
        password_hash=user_data['password_hash'],
        full_name=user_data['full_name'],
        role=user_data['role']
    )

# Clase para Google Drive
class GoogleDriveService:
    def __init__(self):
        self.service = None
        self.initialize_service()
    
    def initialize_service(self):
        try:
            credentials_file = os.getenv('GOOGLE_DRIVE_CREDENTIALS')
            folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
            
            if not credentials_file or not folder_id:
                print("Error: Missing Google Drive credentials or folder ID")
                return
            
            SCOPES = ['https://www.googleapis.com/auth/drive']
            credentials = service_account.Credentials.from_service_account_file(
                credentials_file, scopes=SCOPES)
            self.service = build('drive', 'v3', credentials=credentials)
        except Exception as e:
            print(f"Error initializing Drive service: {e}")
    
    def list_images(self):
        if not self.service:
            return []
        
        folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        results = []
        
        try:
            # Buscar imágenes en la carpeta que no han sido clasificadas
            query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png') and not name contains 'buena_' and not name contains 'mala_' and trashed=false"
            
            response = self.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, mimeType, createdTime)',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            
            results = response.get('files', [])
        except Exception as e:
            print(f"Error listing images: {e}")
        
        return results
    
    def get_image(self, file_id):
        if not self.service:
            return None
        
        try:
            request = self.service.files().get_media(fileId=file_id)
            file_content = io.BytesIO()
            downloader = MediaIoBaseDownload(file_content, request)
            
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            
            file_content.seek(0)
            return file_content.read()
        except Exception as e:
            print(f"Error downloading image: {e}")
            return None
    
    def classify_image(self, file_id, classification, original_name):
        if not self.service:
            return False
        
        try:
            # Crear nuevo nombre con el prefijo de clasificación
            new_name = f"{classification}_{original_name}"
            
            # Actualizar el nombre del archivo
            self.service.files().update(
                fileId=file_id,
                body={'name': new_name},
                supportsAllDrives=True
            ).execute()
            
            return True
        except Exception as e:
            print(f"Error classifying image: {e}")
            return False

# Instanciar el servicio de Google Drive
drive_service = GoogleDriveService()

# Rutas de la aplicación
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user_data = db.users.find_one({"username": username})
        
        if user_data and check_password_hash(user_data['password_hash'], password):
            user = User(
                id=user_data['_id'],
                username=user_data['username'],
                password_hash=user_data['password_hash'],
                full_name=user_data['full_name'],
                role=user_data['role']
            )
            login_user(user)
            return redirect(url_for('dashboard'))
        
        flash('Usuario o contraseña incorrectos', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Obtener estadísticas de clasificación
    stats = {
        'total': db.classifications.count_documents({}),
        'buenas': db.classifications.count_documents({"classification": "buena"}),
        'malas': db.classifications.count_documents({"classification": "mala"}),
        'pendientes': len(drive_service.list_images())
    }
    
    return render_template('dashboard.html', user=current_user, stats=stats)

@app.route('/classify')
@login_required
def classify():
    return render_template('classify.html', user=current_user)

@app.route('/api/get_image')
@login_required
def get_image():
    images = drive_service.list_images()
    
    if not images:
        return jsonify({
            'success': False,
            'message': 'No hay imágenes pendientes de clasificación'
        })
    
    # Tomar la primera imagen
    image = images[0]
    image_data = drive_service.get_image(image['id'])
    
    if not image_data:
        return jsonify({
            'success': False,
            'message': 'Error al descargar la imagen'
        })
    
    # Convertir la imagen a base64 para mostrarla en el navegador
    image_b64 = base64.b64encode(image_data).decode('utf-8')
    
    # Determinar el tipo MIME
    mime_type = image['mimeType']
    data_url = f"data:{mime_type};base64,{image_b64}"
    
    # Formatear fecha de creación
    created_time = datetime.datetime.fromisoformat(image['createdTime'].replace('Z', '+00:00'))
    formatted_time = created_time.strftime("%d/%m/%Y %H:%M:%S")
    
    return jsonify({
        'success': True,
        'file_id': image['id'],
        'name': image['name'],
        'created_time': formatted_time,
        'image_data': data_url,
        'remaining': len(images)
    })

@app.route('/api/classify_image', methods=['POST'])
@login_required
def classify_image():
    data = request.json
    file_id = data.get('file_id')
    classification = data.get('classification')
    original_name = data.get('name')
    
    if not all([file_id, classification, original_name]):
        return jsonify({
            'success': False,
            'message': 'Datos incompletos'
        })
    
    # Clasificar la imagen en Google Drive
    success = drive_service.classify_image(file_id, classification, original_name)
    
    if success:
        # Registrar la clasificación en la base de datos
        db.classifications.insert_one({
            'file_id': file_id,
            'original_name': original_name,
            'classification': classification,
            'classified_by': current_user.id,
            'classified_at': datetime.datetime.utcnow()
        })
        
        return jsonify({
            'success': True,
            'message': f'Imagen clasificada como {classification}'
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Error al clasificar la imagen'
        })

@app.route('/admin')
@login_required
def admin():
    if current_user.role != 'admin':
        flash('No tienes permiso para acceder a esta página', 'danger')
        return redirect(url_for('dashboard'))
    
    users = list(db.users.find({}, {'password_hash': 0}))
    
    return render_template('admin.html', user=current_user, users=users)

@app.route('/api/add_user', methods=['POST'])
@login_required
def add_user():
    if current_user.role != 'admin':
        return jsonify({
            'success': False,
            'message': 'No tienes permiso para realizar esta acción'
        })
    
    data = request.json
    username = data.get('username')
    password = data.get('password')
    full_name = data.get('full_name')
    role = data.get('role', 'user')
    
    if not all([username, password, full_name]):
        return jsonify({
            'success': False,
            'message': 'Datos incompletos'
        })
    
    # Verificar si el usuario ya existe
    existing_user = db.users.find_one({"username": username})
    if existing_user:
        return jsonify({
            'success': False,
            'message': 'El nombre de usuario ya existe'
        })
    
    # Crear el nuevo usuario
    user_id = str(uuid.uuid4())
    db.users.insert_one({
        '_id': user_id,
        'username': username,
        'password_hash': generate_password_hash(password),
        'full_name': full_name,
        'role': role,
        'created_at': datetime.datetime.utcnow()
    })
    
    return jsonify({
        'success': True,
        'message': 'Usuario creado exitosamente'
    })

# Inicializar base de datos con un usuario admin si no existe
def init_db():
    if db.users.count_documents({}) == 0:
        # Crear usuario admin por defecto
        admin_id = str(uuid.uuid4())
        db.users.insert_one({
            '_id': admin_id,
            'username': 'admin',
            'password_hash': generate_password_hash('admin123'),  # Cambiar en producción
            'full_name': 'Administrador',
            'role': 'admin',
            'created_at': datetime.datetime.utcnow()
        })
        print("Usuario admin creado con éxito. Username: admin, Password: admin123")

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
