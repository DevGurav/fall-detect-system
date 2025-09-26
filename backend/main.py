import os
import json
import logging
from datetime import datetime
import joblib
import numpy as np
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Firebase Admin SDK
def initialize_firebase():
    """Initialize Firebase Admin SDK"""
    try:
        if not firebase_admin._apps:
            # Use service account key from environment variable
            service_account_info = json.loads(os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY', '{}'))
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
        
        # Initialize Firestore client
        db = firestore.client()
        logger.info("Firebase initialized successfully")
        return db
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {str(e)}")
        return None

# Global variables
db = None
fall_model = None

def load_ml_model():
    """Load the pre-trained fall detection model"""
    global fall_model
    try:
        model_path = os.path.join(os.path.dirname(__file__), 'fall_model.pkl')
        if os.path.exists(model_path):
            fall_model = joblib.load(model_path)
            logger.info("Fall detection model loaded successfully")
        else:
            logger.warning("Fall model not found. Creating dummy model for testing.")
            # Create a dummy model for demonstration
            from sklearn.ensemble import RandomForestClassifier
            fall_model = RandomForestClassifier(n_estimators=100, random_state=42)
            # Create dummy training data (6 features: accelX, accelY, accelZ, gyroX, gyroY, gyroZ)
            X_dummy = np.random.randn(100, 6)
            y_dummy = np.random.randint(0, 2, 100)
            fall_model.fit(X_dummy, y_dummy)
            logger.info("Dummy model created for testing")
    except Exception as e:
        logger.error(f"Failed to load ML model: {str(e)}")
        fall_model = None

def predict_fall(sensor_data):
    """Predict if fall occurred based on sensor data"""
    if fall_model is None:
        logger.error("No model available for prediction")
        return False
    
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
            'timestamp': firestore.SERVER_TIMESTAMP,
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
        # Create notification message
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title='🚨 Fall Detected!',
                body='A fall has been detected. Please check immediately.'
            ),
            data={
                'event_id': event_id,
                'type': 'fall_detection',
                'timestamp': datetime.utcnow().isoformat()
            },
            tokens=device_tokens,
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
        
        # Send notification
        response = messaging.send_multicast(message)
        logger.info(f"Notification sent successfully. Success: {response.success_count}, Failed: {response.failure_count}")
        
        return response.success_count > 0
        
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
                'registered_at': firestore.SERVER_TIMESTAMP,
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

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'model_loaded': fall_model is not None,
        'firebase_connected': db is not None,
        'timestamp': datetime.utcnow().isoformat()
    })

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
        events_ref = db.collection('fall_events').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)
        
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

if __name__ == '__main__':
    # Initialize Firebase and load model
    db = initialize_firebase()
    load_ml_model()
    
    # Run the app
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)