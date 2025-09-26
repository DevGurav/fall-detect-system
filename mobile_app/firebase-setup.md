# Firebase Configuration Files

This directory should contain the Firebase configuration files for your project.

## Required Files:

### For Android:
- `android/app/google-services.json`

### For iOS:
- `ios/Runner/GoogleService-Info.plist`

### For Web (optional):
- `web/firebase-config.js`

## How to get these files:

1. Go to the [Firebase Console](https://console.firebase.google.com/)
2. Select your project
3. Go to Project Settings (gear icon)
4. Under "Your apps", add your Flutter app for each platform
5. Download the configuration files for each platform
6. Place them in the correct directories as shown above

## Additional Setup:

### Android (android/app/build.gradle):
Add these lines to your `android/app/build.gradle`:

```gradle
plugins {
    id 'com.android.application'
    id 'kotlin-android'
    id 'dev.flutter.flutter-gradle-plugin'
    id 'com.google.gms.google-services'  // Add this line
}
```

### iOS (ios/Runner.xcworkspace):
The GoogleService-Info.plist file should be added to your iOS project through Xcode.

### Firebase Project Setup:
1. Enable Firestore Database
2. Enable Cloud Messaging
3. Set up your security rules for Firestore
4. Generate a service account key for the backend

## Environment Variables for Backend:
Create a `.env` file in the backend directory with your Firebase service account key:

```
FIREBASE_SERVICE_ACCOUNT_KEY={"type": "service_account", "project_id": "your-project-id", ...}
```