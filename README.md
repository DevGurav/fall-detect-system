# AI-Based Fall Risk Detection System

A comprehensive fall detection system using ESP32 wristband sensors, cloud-based AI prediction, and real-time mobile notifications.

## 🚀 System Overview

This system detects falls using a wristband (ESP32 + MPU6050) and notifies caregivers in real-time through a mobile app. The system uses a trained AI model deployed in the cloud to analyze sensor data and determine if a fall has occurred.

## 🏗️ Architecture

```
ESP32 Device → Flask API → AI Model → Firebase → Mobile App
```

1. **ESP32** collects accelerometer + gyroscope data from MPU6050
2. When suspicious activity is detected, ESP32 sends data via HTTP POST to cloud API
3. **Cloud API** loads pre-trained ML model (RandomForest/MLP) and predicts if a fall occurred
4. If fall is confirmed → API triggers Firebase Cloud Messaging (FCM) push notification
5. **Mobile App** (Flutter) receives notification even when closed/backgrounded
6. App shows fall event history stored in Firestore

## 📁 Project Structure

```
fall-detection-system/
├── backend/                 # Flask API server
│   ├── main.py             # Main Flask application
│   ├── requirements.txt    # Python dependencies
│   ├── .env.example       # Environment variables template
│   └── Procfile           # Deployment configuration
├── mobile_app/             # Flutter mobile application
│   ├── lib/
│   │   ├── models/        # Data models
│   │   ├── services/      # Firebase service
│   │   ├── screens/       # UI screens
│   │   ├── widgets/       # Reusable UI components
│   │   └── main.dart      # App entry point
│   └── pubspec.yaml       # Flutter dependencies
└── README.md              # This file
```

## 🛠️ Tech Stack

- **Device**: ESP32 (Wi-Fi), MPU6050 accelerometer/gyroscope
- **Backend**: Python + Flask, joblib (ML model), Firebase Admin SDK
- **Database**: Firebase Firestore (event logs & device tokens)
- **Notifications**: Firebase Cloud Messaging (FCM)
- **Mobile App**: Flutter (cross-platform, Android/iOS)
- **Hosting**: Free tier cloud hosting (Render/Railway) + Firebase free tier

## 🚀 Quick Start

### Backend Setup

1. **Navigate to backend directory**:
   ```bash
   cd backend
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Firebase**:
   - Create a Firebase project at https://console.firebase.google.com/
   - Generate a service account key (Settings → Service accounts → Generate new private key)
   - Copy `.env.example` to `.env` and add your Firebase service account key

4. **Run the server**:
   ```bash
   python main.py
   ```

### Mobile App Setup

1. **Navigate to mobile app directory**:
   ```bash
   cd mobile_app
   ```

2. **Install Flutter dependencies**:
   ```bash
   flutter pub get
   ```

3. **Configure Firebase**:
   - Add your Firebase configuration files (see `firebase-setup.md`)
   - Update Firebase project settings in the app

4. **Run the app**:
   ```bash
   flutter run
   ```

## 📱 Features

### Backend API
- ✅ `/predict` - Accepts sensor data and returns fall prediction
- ✅ `/register-device` - Registers mobile devices for notifications
- ✅ `/health` - Health check endpoint
- ✅ `/fall-events` - Retrieves fall event history
- ✅ Automatic Firestore logging of all events
- ✅ Real-time FCM notifications for fall alerts

### Mobile App
- ✅ Real-time fall event monitoring
- ✅ Background notification handling (app closed/minimized)
- ✅ Local notifications with sound and vibration
- ✅ Fall event history with timestamps
- ✅ Device statistics dashboard
- ✅ Event acknowledgment system
- ✅ Automatic Firebase synchronization

### ESP32 Integration
The ESP32 device should send HTTP POST requests to your backend `/predict` endpoint with this JSON format:

```json
{
  "device_id": "esp32_001",
  "accelX": 0.123,
  "accelY": 0.456,
  "accelZ": 0.789,
  "gyroX": 1.234,
  "gyroY": 5.678,
  "gyroZ": 9.012
}
```

## 🔧 Configuration

### Environment Variables (Backend)

Create a `.env` file in the backend directory:

```env
FIREBASE_SERVICE_ACCOUNT_KEY={"type": "service_account", ...}
FLASK_ENV=production
SECRET_KEY=your-secret-key
PORT=5000
```

### Firebase Setup

1. **Enable required services**:
   - Firestore Database
   - Cloud Messaging
   - Authentication (optional)

2. **Firestore Security Rules**:
   ```javascript
   rules_version = '2';
   service cloud.firestore {
     match /databases/{database}/documents {
       match /fall_events/{document} {
         allow read, write: if true;
       }
       match /devices/{document} {
         allow read, write: if true;
       }
     }
   }
   ```

## 🌐 Deployment

### Backend Deployment (Render/Railway)

1. **Create a new web service**
2. **Connect your GitHub repository**
3. **Set environment variables**:
   - `FIREBASE_SERVICE_ACCOUNT_KEY`
   - `FLASK_ENV=production`
4. **The service will automatically use the Procfile for deployment**

### Mobile App Deployment

1. **Android**:
   ```bash
   flutter build apk --release
   ```

2. **iOS**:
   ```bash
   flutter build ios --release
   ```

## 🔔 Notification Flow

1. ESP32 detects suspicious motion → sends data to API
2. API runs ML prediction → if fall detected, stores event in Firestore
3. API retrieves FCM tokens from devices collection
4. API sends push notification to all registered devices
5. Mobile app receives notification (even if closed)
6. App shows local notification with sound/vibration
7. User can view event details and acknowledge

## 🤖 Machine Learning Model

The system uses a pre-trained ML model (`fall_model.pkl`) that should be placed in the backend directory. The model expects 6 features:
- accelX, accelY, accelZ (accelerometer data)
- gyroX, gyroY, gyroZ (gyroscope data)

For testing purposes, a dummy RandomForest model is created automatically if no model file is found.

## 📊 API Endpoints

### POST /predict
Analyze sensor data for fall detection.

**Request Body**:
```json
{
  "device_id": "string",
  "accelX": "number",
  "accelY": "number", 
  "accelZ": "number",
  "gyroX": "number",
  "gyroY": "number",
  "gyroZ": "number"
}
```

**Response**:
```json
{
  "fall": "boolean",
  "confidence": "number",
  "event_id": "string",
  "timestamp": "string",
  "notification_sent": "boolean"
}
```

### POST /register-device
Register a device for FCM notifications.

**Request Body**:
```json
{
  "device_id": "string",
  "fcm_token": "string"
}
```

### GET /fall-events
Retrieve fall event history.

**Query Parameters**:
- `limit` (optional): Number of events to return (default: 50)
- `device_id` (optional): Filter by specific device

## 🛡️ Security Considerations

- Use HTTPS for all API communication
- Implement proper Firebase security rules
- Store sensitive configuration in environment variables
- Regular security updates for all dependencies
- Consider implementing API authentication for production use

## 🔍 Troubleshooting

### Common Issues

1. **Firebase initialization fails**:
   - Check service account key format
   - Verify Firebase project configuration
   - Ensure required Firebase services are enabled

2. **Notifications not received**:
   - Check FCM token registration
   - Verify notification permissions
   - Test with Firebase console

3. **App crashes on startup**:
   - Run `flutter clean && flutter pub get`
   - Check Firebase configuration files
   - Verify minimum SDK versions

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 📞 Support

For support, please create an issue in the GitHub repository or contact the development team.

---

**Note**: This system is designed for monitoring purposes and should not be used as the sole method of fall detection for critical safety applications. Always have backup safety measures in place.