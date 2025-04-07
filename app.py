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

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(16))

# Fixed credentials (replace with your preferred ones)
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')

# Class for Google Drive
class GoogleDriveService:
    def __init__(self):
        self.service = None
        self.initialize_service()
    # Set up connection to Google Drive using credentials
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
    # Get a list of unclassified images in the folder
    def list_images(self, filter_date=None):
        if not self.service:
            return []
        
        folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        results = []
        
        try:
            # Search for images in the folder that haven't been classified
            query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png') and not name contains 'good_' and not name contains 'bad_' and trashed=false"
            
            response = self.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, mimeType, createdTime)',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            
            results = response.get('files', [])
            
            # Apply date filter if provided
            if filter_date:
                filter_date_obj = datetime.datetime.strptime(filter_date, '%Y-%m-%d')
                filter_date_start = filter_date_obj.replace(hour=0, minute=0, second=0).isoformat() + 'Z'
                filter_date_end = filter_date_obj.replace(hour=23, minute=59, second=59).isoformat() + 'Z'
                
                results = [
                    file for file in results 
                    if filter_date_start <= file['createdTime'] <= filter_date_end
                ]
                
        except Exception as e:
            print(f"Error listing images: {e}")
        
        return results
    # Download a specific image
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
    # Rename an image adding a prefix (good_ or bad_)
    def classify_image(self, file_id, classification, original_name):
        if not self.service:
            return False
        
        try:
            # Create new name with classification prefix
            new_name = f"{classification}_{original_name}"
            
            # Update file name
            self.service.files().update(
                fileId=file_id,
                body={'name': new_name},
                supportsAllDrives=True
            ).execute()
            
            return True
        except Exception as e:
            print(f"Error classifying image: {e}")
            return False
    
    # Method for counting statistics
    def get_stats(self, filter_date=None):
        if not self.service:
            return {
                'total': 0,
                'good': 0,
                'bad': 0,
                'pending': 0
            }
        
        folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        
        try:
            date_filter = ""
            if filter_date:
                filter_date_obj = datetime.datetime.strptime(filter_date, '%Y-%m-%d')
                filter_date_start = filter_date_obj.replace(hour=0, minute=0, second=0).isoformat() + 'Z'
                filter_date_end = filter_date_obj.replace(hour=23, minute=59, second=59).isoformat() + 'Z'
                date_filter = f" and createdTime >= '{filter_date_start}' and createdTime <= '{filter_date_end}'"
            
            # Count pending images
            pending_query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png') and not name contains 'good_' and not name contains 'bad_' and trashed=false{date_filter}"
            pending_response = self.service.files().list(q=pending_query, spaces='drive', fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            pending_count = len(pending_response.get('files', []))
            
            # Count images classified as good
            good_query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png') and name contains 'good_' and trashed=false{date_filter}"
            good_response = self.service.files().list(q=good_query, spaces='drive', fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            good_count = len(good_response.get('files', []))
            
            # Count images classified as bad
            bad_query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png') and name contains 'bad_' and trashed=false{date_filter}"
            bad_response = self.service.files().list(q=bad_query, spaces='drive', fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            bad_count = len(bad_response.get('files', []))
            
            # Calculate total
            total_count = pending_count + good_count + bad_count
            
            return {
                'total': total_count,
                'good': good_count,
                'bad': bad_count,
                'pending': pending_count
            }
        except Exception as e:
            print(f"Error getting stats: {e}")
            return {
                'total': 0,
                'good': 0,
                'bad': 0,
                'pending': 0
            }

# Instantiate Google Drive service
drive_service = GoogleDriveService()

# Decorator function to require authentication
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Application routes
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
        
        flash('Incorrect username or password', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Get filter date from query parameters
    filter_date = request.args.get('filter_date')
    
    # Get statistics from Google Drive
    stats = drive_service.get_stats(filter_date)
    return render_template('dashboard.html', username=session['username'], stats=stats, filter_date=filter_date)

@app.route('/classify')
@login_required
def classify():
    # Get filter date from query parameters
    filter_date = request.args.get('filter_date')
    return render_template('classify.html', username=session['username'], filter_date=filter_date)

@app.route('/api/get_image')
@login_required
def get_image():
    # Get filter date from query parameters
    filter_date = request.args.get('filter_date')
    images = drive_service.list_images(filter_date)
    
    if not images:
        return jsonify({
            'success': False,
            'message': 'No pending images to classify'
        })
    
    # Take the first image
    image = images[0]
    image_data = drive_service.get_image(image['id'])
    
    if not image_data:
        return jsonify({
            'success': False,
            'message': 'Error downloading the image'
        })
    
    # Convert image to base64 to display in browser
    image_b64 = base64.b64encode(image_data).decode('utf-8')
    
    # Determine MIME type
    mime_type = image['mimeType']
    data_url = f"data:{mime_type};base64,{image_b64}"
    
    # Format creation date
    created_time = datetime.datetime.fromisoformat(image['createdTime'].replace('Z', '+00:00'))
    formatted_time = created_time.strftime("%m/%d/%Y %H:%M:%S")
    
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
            'message': 'Incomplete data'
        })
    
    # Classify the image in Google Drive
    success = drive_service.classify_image(file_id, classification, original_name)
    
    if success:
        return jsonify({
            'success': True,
            'message': f'Image classified as {classification}'
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Error classifying the image'
        })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)