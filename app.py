# app.py 
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import os
import io
import datetime
import secrets
from functools import wraps
from dotenv import load_dotenv
import base64

# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(16))

# Credenciales fijas (reemplaza con las que prefieras)
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')

# Clase para Google Drive
class GoogleDriveService:
    def __init__(self):
        self.service = None
        self.initialize_service()
    # Configura la conexión a Google Drive usando las credenciales
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
    #Obtiene una lista de imágenes no clasificadas en la carpeta
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
    # Descarga una imagen específica
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
    # Renombra una imagen añadiendo un prefijo (buena_ o mala_)
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
    
    # Método para contar estadísticas 
    def get_stats(self):
        if not self.service:
            return {
                'total': 0,
                'buenas': 0,
                'malas': 0,
                'pendientes': 0
            }
        
        folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        
        try:
            # Contar imágenes pendientes
            pending_query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png') and not name contains 'buena_' and not name contains 'mala_' and trashed=false"
            pending_response = self.service.files().list(q=pending_query, spaces='drive', fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            pending_count = len(pending_response.get('files', []))
            
            # Contar imágenes clasificadas como buenas
            good_query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png') and name contains 'buena_' and trashed=false"
            good_response = self.service.files().list(q=good_query, spaces='drive', fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            good_count = len(good_response.get('files', []))
            
            # Contar imágenes clasificadas como malas
            bad_query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png') and name contains 'mala_' and trashed=false"
            bad_response = self.service.files().list(q=bad_query, spaces='drive', fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            bad_count = len(bad_response.get('files', []))
            
            # Calcular total
            total_count = pending_count + good_count + bad_count
            
            return {
                'total': total_count,
                'buenas': good_count,
                'malas': bad_count,
                'pendientes': pending_count
            }
        except Exception as e:
            print(f"Error getting stats: {e}")
            return {
                'total': 0,
                'buenas': 0,
                'malas': 0,
                'pendientes': 0
            }

# Instanciar el servicio de Google Drive
drive_service = GoogleDriveService()

# Función decoradora para requerir autenticación
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Rutas de la aplicación
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('dashboard'))
        
        flash('Usuario o contraseña incorrectos', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Obtener estadísticas desde Google Drive
    stats = drive_service.get_stats()
    return render_template('dashboard.html', username=session['username'], stats=stats)

@app.route('/classify')
@login_required
def classify():
    return render_template('classify.html', username=session['username'])

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
        return jsonify({
            'success': True,
            'message': f'Imagen clasificada como {classification}'
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Error al clasificar la imagen'
        })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)