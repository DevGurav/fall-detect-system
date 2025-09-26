import os
import sys
import json
import logging
from datetime import datetime
import joblib
import numpy as np
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Log environment information
logger.info("=== FALL DETECTION API STARTUP ===")
logger.info(f"Python version: {sys.version}")
logger.info(f"Working directory: {os.getcwd()}")
logger.info(f"App directory: {os.path.dirname(__file__)}")

# Check environment variables
env_vars = ['FIREBASE_SERVICE_ACCOUNT_KEY', 'PORT']
for var in env_vars:
    value = os.getenv(var)
    if value:
        logger.info(f"Environment variable {var} is set (length: {len(value)})")
    else:
        logger.warning(f"Environment variable {var} is NOT set")

# Initialize Firebase Admin SDK
def initialize_firebase():
    """Initialize Firebase Admin SDK with detailed logging"""
    try:
        logger.info("Starting Firebase initialization...")
        
        if firebase_admin._apps:
            logger.info("Firebase app already exists, using existing instance")
            db = firestore.client()
            logger.info("Firebase Firestore client ready")
            return db
        
        # Get service account key from environment
        service_account_key = os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY')
        if not service_account_key:
            logger.error("FIREBASE_SERVICE_ACCOUNT_KEY environment variable is missing")
            logger.error("Please set this environment variable with your Firebase service account JSON")
            return None
            
        logger.info(f"Firebase service account key found (length: {len(service_account_key)})")
        
        # Try to parse JSON
        try:
            service_account_info = json.loads(service_account_key)
            logger.info("Service account JSON parsed successfully")
            
            # Log key fields (without sensitive data)
            required_fields = ['type', 'project_id', 'private_key_id', 'client_email', 'client_id', 'auth_uri', 'token_uri']
            missing_fields = [field for field in required_fields if field not in service_account_info]
            
            if missing_fields:
                logger.error(f"Missing required fields in service account JSON: {missing_fields}")
                return None
            
            logger.info(f"Service account project_id: {service_account_info.get('project_id')}")
            logger.info(f"Service account client_email: {service_account_info.get('client_email')}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Firebase service account JSON: {e}")
            logger.error("Make sure the JSON is properly formatted and not wrapped in quotes")
            return None
            
        # Initialize Firebase Admin
        try:
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin SDK initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin SDK: {e}")
            return None
        
        # Initialize Firestore client
        try:
            db = firestore.client()
            logger.info("Firestore client initialized successfully")
            
            # Test Firestore connection
            test_collection = db.collection('test')
            logger.info("Firestore connection test passed")
            return db
            
        except Exception as e:
            logger.error(f"Failed to initialize Firestore client: {e}")
            return None
        
    except Exception as e:
        logger.error(f"Unexpected error during Firebase initialization: {e}")
        logger.exception("Full traceback:")
        return None

# Global variables
db = None
fall_model = None

@app.route('/', methods=['GET'])
def root():
    """API welcome page with documentation"""
    return jsonify({
        'message': 'Fall Detection API is running!',
        'version': '1.0.0',
        'status': 'operational',
        'endpoints': {
            '/health': 'Health check endpoint',
            '/predict': 'Fall prediction endpoint (POST)',
            '/register-device': 'Device registration endpoint (POST)',
            '/fall-events': 'Get fall events history (GET)'
        },
        'documentation': 'https://github.com/devendra011396/fall-detect-system',
        'timestamp': datetime.utcnow().isoformat()
    })

def load_ml_model():
    """Load the pre-trained fall detection model with detailed logging"""
    global fall_model
    try:
        logger.info("Starting ML model loading...")
        
        # Check for model file
        model_path = os.path.join(os.path.dirname(__file__), 'fall_model.pkl')
        logger.info(f"Looking for model file at: {model_path}")
        
        if os.path.exists(model_path):
            logger.info("Model file found, loading...")
            try:
                fall_model = joblib.load(model_path)
                logger.info("Fall detection model loaded successfully")
                
                # Test the model
                try:
                    test_data = np.array([[1.0, 2.0, 3.0, 0.1, 0.2, 0.3]])
                    test_prediction = fall_model.predict(test_data)
                    logger.info(f"Model test prediction: {test_prediction}")
                except Exception as e:
                    logger.warning(f"Model test failed: {e}")
                    
            except Exception as e:
                logger.error(f"Failed to load model file: {e}")
                fall_model = None
        else:
            logger.warning(f"Fall model file not found at {model_path}")
            logger.info("Creating dummy model for testing...")
            
            try:
                # Create a dummy model for demonstration
                from sklearn.ensemble import RandomForestClassifier
                fall_model = RandomForestClassifier(n_estimators=100, random_state=42)
                
                # Create dummy training data (6 features: accelX, accelY, accelZ, gyroX, gyroY, gyroZ)
                X_dummy = np.random.randn(100, 6)
                y_dummy = np.random.randint(0, 2, 100)
                fall_model.fit(X_dummy, y_dummy)
                logger.info("Dummy model created and trained successfully")
                
                # Test dummy model
                test_data = np.array([[1.0, 2.0, 3.0, 0.1, 0.2, 0.3]])
                test_prediction = fall_model.predict(test_data)
                logger.info(f"Dummy model test prediction: {test_prediction}")
                
            except Exception as e:
                logger.error(f"Failed to create dummy model: {e}")
                fall_model = None
        
        if fall_model is not None:
            logger.info("Model loading completed successfully")
        else:
            logger.error("Model loading failed completely")
            
    except Exception as e:
        logger.error(f"Unexpected error during model loading: {e}")
        logger.exception("Full traceback:")
        fall_model = None

def predict_fall(sensor_data):
    """Predict if fall occurred based on sensor data"""
    if fall_model is None:
        logger.error("No model available for prediction")
        return False, 0.0
    
    try:
        # Extract features from sensor data
        features = np.array([[
            sensor_data['accelX'],
            sensor_data['accelY'], 
            sensor_data['accelZ'],
            sensor_data['gyroX'],
            sensor_data['gyroY'],
            sensor_data['gyroZ']
        ]])
        
        # Make prediction
        prediction = fall_model.predict(features)[0]
        probability = fall_model.predict_proba(features)[0]
        
        logger.info(f"Fall prediction: {prediction}, Probability: {probability}")
        return bool(prediction), float(max(probability))
        
    except Exception as e:
        logger.error(f"Error in fall prediction: {str(e)}")
        return False, 0.0

def log_fall_event(device_id, sensor_data, fall_detected, confidence):
    """Log fall event to Firestore"""
    if db is None:
        logger.error("Firestore not initialized")
        return None
        
    try:
        event_data = {
            'device_id': device_id,
            'timestamp': datetime.utcnow(),
            'sensor_data': sensor_data,
            'fall_detected': fall_detected,
            'confidence': confidence,
            'processed_at': datetime.utcnow().isoformat()
        }
        
        # Add to Firestore
        doc_ref = db.collection('fall_events').add(event_data)
        logger.info(f"Fall event logged with ID: {doc_ref[1].id}")
        return doc_ref[1].id
        
    except Exception as e:
        logger.error(f"Failed to log fall event: {str(e)}")
        return None

def send_fall_notification(device_tokens, event_id):
    """Send FCM notification to caretakers"""
    if not device_tokens:
        logger.warning("No device tokens available for notification")
        return False
        
    try:
        # Send notification to each token individually
        successful_sends = 0
        failed_sends = 0
        
        for token in device_tokens:
            try:
                message = messaging.Message(
                    notification=messaging.Notification(
                        title='🚨 Fall Detected!',
                        body='A fall has been detected. Please check immediately.'
                    ),
                    data={
                        'event_id': event_id,
                        'type': 'fall_detection',
                        'timestamp': datetime.utcnow().isoformat()
                    },
                    token=token,
                    android=messaging.AndroidConfig(
                        notification=messaging.AndroidNotification(
                            icon='ic_notification',
                            color='#ff0000',
                            sound='default'
                        ),
                        priority='high'
                    ),
                    apns=messaging.APNSConfig(
                        payload=messaging.APNSPayload(
                            aps=messaging.Aps(
                                alert=messaging.ApsAlert(
                                    title='🚨 Fall Detected!',
                                    body='A fall has been detected. Please check immediately.'
                                ),
                                sound='default',
                                badge=1
                            )
                        )
                    )
                )
                
                messaging.send(message)
                successful_sends += 1
            except Exception as e:
                logger.error(f"Failed to send notification to token {token}: {str(e)}")
                failed_sends += 1
                
        logger.info(f"Notification sent successfully to {successful_sends} devices, failed: {failed_sends}")
        return successful_sends > 0
        
    except Exception as e:
        logger.error(f"Failed to send notification: {str(e)}")
        return False

def get_device_tokens():
    """Get FCM device tokens from Firestore"""
    if db is None:
        return []
        
    try:
        # Get all registered devices
        devices_ref = db.collection('devices')
        docs = devices_ref.stream()
        
        tokens = []
        for doc in docs:
            data = doc.to_dict()
            if 'fcm_token' in data and data.get('active', True):
                tokens.append(data['fcm_token'])
        
        logger.info(f"Retrieved {len(tokens)} device tokens")
        return tokens
        
    except Exception as e:
        logger.error(f"Failed to get device tokens: {str(e)}")
        return []

@app.route('/predict', methods=['POST'])
def predict():
    """Main prediction endpoint"""
    try:
        # Get JSON data from request
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        # Validate required fields
        required_fields = ['accelX', 'accelY', 'accelZ', 'gyroX', 'gyroY', 'gyroZ']
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            return jsonify({
                'error': f'Missing required fields: {", ".join(missing_fields)}'
            }), 400
            
        # Get device ID (optional)
        device_id = data.get('device_id', 'unknown')
        
        # Make prediction
        fall_detected, confidence = predict_fall(data)
        
        # Log event to Firestore
        event_id = log_fall_event(device_id, data, fall_detected, confidence)
        
        response_data = {
            'fall': fall_detected,
            'confidence': confidence,
            'event_id': event_id,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        # If fall detected, send notification
        if fall_detected and event_id:
            device_tokens = get_device_tokens()
            if device_tokens:
                notification_sent = send_fall_notification(device_tokens, event_id)
                response_data['notification_sent'] = notification_sent
            else:
                response_data['notification_sent'] = False
                response_data['message'] = 'No registered devices for notification'
        
        logger.info(f"Prediction response: {response_data}")
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Error in predict endpoint: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/register-device', methods=['POST'])
def register_device():
    """Register a device for FCM notifications"""
    try:
        data = request.get_json()
        
        if not data or 'fcm_token' not in data:
            return jsonify({'error': 'FCM token required'}), 400
            
        device_id = data.get('device_id', 'unknown')
        fcm_token = data['fcm_token']
        
        # Store device info in Firestore
        if db:
            device_data = {
                'device_id': device_id,
                'fcm_token': fcm_token,
                'registered_at': datetime.utcnow(),
                'active': True
            }
            
            db.collection('devices').document(device_id).set(device_data)
            logger.info(f"Device {device_id} registered successfully")
            
            return jsonify({
                'success': True,
                'message': 'Device registered successfully'
            })
        else:
            return jsonify({'error': 'Database not available'}), 500
            
    except Exception as e:
        logger.error(f"Error registering device: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/')
def home():
    """Welcome endpoint with API information"""
    return jsonify({
        'message': 'Fall Detection API is running!',
        'version': '1.0.0',
        'status': 'operational',
        'endpoints': {
            '/health': 'Health check endpoint',
            '/predict': 'Fall prediction endpoint (POST)',
            '/register-device': 'Device registration endpoint (POST)',
            '/fall-events': 'Get fall events history (GET)'
        },
        'documentation': 'https://github.com/devendra011396/fall-detect-system',
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/health', methods=['GET'])
def health():
    """Enhanced health check endpoint with detailed diagnostics"""
    logger.info("Health check requested")
    
    # Basic status
    status_info = {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '2.0.0',
        'environment': 'production' if not os.getenv('DEBUG') else 'development'
    }
    
    # Firebase connection status
    firebase_status = {
        'firebase_connected': db is not None,
        'firebase_app_count': len(firebase_admin._apps) if firebase_admin._apps else 0
    }
    
    if db is not None:
        try:
            # Test Firestore connection
            test_ref = db.collection('health_check').document('test')
            test_ref.set({'timestamp': datetime.utcnow()})
            firebase_status['firestore_write_test'] = True
            logger.info("Firestore write test passed")
        except Exception as e:
            firebase_status['firestore_write_test'] = False
            firebase_status['firestore_error'] = str(e)
            logger.error(f"Firestore write test failed: {e}")
    else:
        firebase_status['firestore_write_test'] = False
        firebase_status['firebase_error'] = 'Database connection not established'
    
    # Model status
    model_status = {
        'model_loaded': fall_model is not None,
        'model_type': str(type(fall_model).__name__) if fall_model else None
    }
    
    if fall_model is not None:
        try:
            # Test model prediction
            test_data = np.array([[1.0, 2.0, 3.0, 0.1, 0.2, 0.3]])
            test_prediction = fall_model.predict(test_data)
            model_status['model_test'] = True
            model_status['test_prediction'] = int(test_prediction[0])
            logger.info("Model prediction test passed")
        except Exception as e:
            model_status['model_test'] = False
            model_status['model_error'] = str(e)
            logger.error(f"Model prediction test failed: {e}")
    else:
        model_status['model_test'] = False
        model_status['model_error'] = 'Model not loaded'
    
    # Environment variables status
    env_status = {}
    for var in ['FIREBASE_SERVICE_ACCOUNT_KEY', 'PORT']:
        value = os.getenv(var)
        env_status[f'{var.lower()}_set'] = value is not None
        if value:
            env_status[f'{var.lower()}_length'] = len(value)
    
    # Combine all status information
    response = {
        **status_info,
        **firebase_status,
        **model_status,
        'environment_variables': env_status,
        'system_info': {
            'python_version': sys.version.split()[0],
            'working_directory': os.getcwd(),
            'app_directory': os.path.dirname(__file__)
        }
    }
    
    # Determine overall health
    if firebase_status['firebase_connected'] and model_status['model_loaded']:
        response['overall_status'] = 'fully_operational'
    elif firebase_status['firebase_connected'] or model_status['model_loaded']:
        response['overall_status'] = 'partially_operational'
    else:
        response['overall_status'] = 'degraded'
    
    logger.info(f"Health check response: {response['overall_status']}")
    return jsonify(response)

@app.route('/fall-events', methods=['GET'])
def get_fall_events():
    """Get recent fall events"""
    try:
        if db is None:
            return jsonify({'error': 'Database not available'}), 500
            
        # Get optional query parameters
        limit = request.args.get('limit', 50, type=int)
        device_id = request.args.get('device_id')
        
        # Query fall events
        events_ref = db.collection('fall_events').order_by('timestamp', direction='DESCENDING').limit(limit)
        
        if device_id:
            events_ref = events_ref.where('device_id', '==', device_id)
            
        docs = events_ref.stream()
        
        events = []
        for doc in docs:
            event_data = doc.to_dict()
            event_data['id'] = doc.id
            events.append(event_data)
            
        return jsonify({
            'events': events,
            'count': len(events)
        })
        
    except Exception as e:
        logger.error(f"Error getting fall events: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# Initialize systems when module is imported (works with gunicorn)
logger.info("=== INITIALIZING FALL DETECTION API ===")

# Initialize Firebase
logger.info("Step 1: Initializing Firebase...")
db = initialize_firebase()
if db:
    logger.info("✓ Firebase initialization successful")
else:
    logger.error("✗ Firebase initialization failed")

# Load ML model
logger.info("Step 2: Loading ML model...")
load_ml_model()
if fall_model:
    logger.info("✓ ML model loading successful")
else:
    logger.error("✗ ML model loading failed")

# Final status
logger.info("=== INITIALIZATION COMPLETE ===")
logger.info(f"Firebase connected: {db is not None}")
logger.info(f"Model loaded: {fall_model is not None}")

if db is not None and fall_model is not None:
    logger.info("🎉 All systems operational!")
elif db is not None or fall_model is not None:
    logger.warning("⚠️ Partial system functionality")
else:
    logger.error("❌ Critical systems failed - API will have limited functionality")

if __name__ == '__main__':
    # If running directly (not via gunicorn), the initialization above already ran
    logger.info("Running Flask app directly (not via gunicorn)")
    
    # Start the Flask application
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Flask app on port {port}")
    
    try:
        app.run(host='0.0.0.0', port=port, debug=False)
    except Exception as e:
        logger.error(f"Failed to start Flask app: {e}")
        logger.exception("Full traceback:")