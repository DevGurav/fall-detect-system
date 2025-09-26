# Flutter Mobile App Setup & Deployment Guide

This guide covers setting up and deploying the Fall Detection Flutter mobile app.

## 📱 Prerequisites

- Flutter SDK (latest stable version)
- Android Studio (for Android development)
- Xcode (for iOS development, macOS only)
- Firebase project with enabled services

## 🔧 Initial Setup

### 1. Flutter Environment
```bash
flutter doctor
# Ensure all required components are installed
```

### 2. Clone and Setup
```bash
cd mobile_app
flutter pub get
```

### 3. Firebase Configuration

#### Create Firebase Project
1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Create new project or use existing
3. Enable these services:
   - **Firestore Database**
   - **Cloud Messaging**
   - **Authentication** (optional)

#### Add Flutter App to Firebase
1. In Firebase Console, click "Add app" → Flutter
2. Follow the setup wizard
3. Download configuration files

### 4. Platform-Specific Setup

#### Android Setup

1. **Add google-services.json**:
   ```bash
   # Place the file in:
   android/app/google-services.json
   ```

2. **Update android/build.gradle**:
   ```gradle
   buildscript {
       dependencies {
           // ... other dependencies
           classpath 'com.google.gms:google-services:4.4.0'
       }
   }
   ```

3. **Update android/app/build.gradle**:
   ```gradle
   plugins {
       id 'com.android.application'
       id 'kotlin-android'
       id 'dev.flutter.flutter-gradle-plugin'
       id 'com.google.gms.google-services'  // Add this line
   }

   android {
       compileSdkVersion 34
       
       defaultConfig {
           minSdkVersion 21  // Required for Firebase
           targetSdkVersion 34
       }
   }

   dependencies {
       implementation 'com.google.firebase:firebase-analytics'
       implementation 'com.google.firebase:firebase-messaging'
   }
   ```

4. **Update AndroidManifest.xml**:
   ```xml
   <!-- android/app/src/main/AndroidManifest.xml -->
   <manifest xmlns:android="http://schemas.android.com/apk/res/android">
       <uses-permission android:name="android.permission.INTERNET" />
       <uses-permission android:name="android.permission.WAKE_LOCK" />
       <uses-permission android:name="android.permission.VIBRATE" />
       <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
       
       <application
           android:label="Fall Detection"
           android:name="${applicationName}"
           android:icon="@mipmap/ic_launcher">
           
           <!-- FCM Service -->
           <service
               android:name="com.google.firebase.messaging.FirebaseMessagingService"
               android:exported="false">
               <intent-filter>
                   <action android:name="com.google.firebase.MESSAGING_EVENT" />
               </intent-filter>
           </service>
           
           <!-- Notification metadata -->
           <meta-data
               android:name="com.google.firebase.messaging.default_notification_icon"
               android:resource="@drawable/ic_notification" />
           <meta-data
               android:name="com.google.firebase.messaging.default_notification_color"
               android:resource="@color/colorAccent" />
       </application>
   </manifest>
   ```

#### iOS Setup

1. **Add GoogleService-Info.plist**:
   - Open `ios/Runner.xcworkspace` in Xcode
   - Drag GoogleService-Info.plist into Runner/Runner folder
   - Ensure "Copy items if needed" is checked

2. **Update ios/Runner/Info.plist**:
   ```xml
   <!-- Add before closing </dict> -->
   <key>FirebaseAppDelegateProxyEnabled</key>
   <false/>
   ```

3. **Enable Push Notifications**:
   - In Xcode: Runner → Signing & Capabilities → "+ Capability"
   - Add "Push Notifications" and "Background Modes"
   - Under Background Modes, check "Background fetch" and "Remote notifications"

## 🚀 Development & Testing

### Run on Device/Emulator
```bash
# List available devices
flutter devices

# Run on specific device
flutter run -d <device-id>

# Run in release mode
flutter run --release
```

### Test Firebase Integration
```bash
# Test FCM (replace with your project)
cd mobile_app
dart pub global activate flutterfire_cli
flutterfire test
```

### Update Backend URL
Update the backend URL in `lib/services/firebase_service.dart`:
```dart
// Replace with your deployed backend URL
const backendUrl = 'https://your-backend-url.onrender.com';
```

## 📦 Building for Production

### Android APK
```bash
# Build APK
flutter build apk --release

# Build App Bundle (recommended for Play Store)
flutter build appbundle --release

# Files will be in:
# build/app/outputs/flutter-apk/app-release.apk
# build/app/outputs/bundle/release/app-release.aab
```

### iOS
```bash
# Build for iOS
flutter build ios --release

# Archive in Xcode:
# 1. Open ios/Runner.xcworkspace in Xcode
# 2. Product → Archive
# 3. Upload to App Store Connect
```

## 🏪 Store Deployment

### Google Play Store

1. **Prepare App Bundle**:
   ```bash
   flutter build appbundle --release
   ```

2. **Create Play Store Listing**:
   - Go to [Google Play Console](https://play.google.com/console)
   - Create new application
   - Upload app bundle
   - Fill in store listing details

3. **Required Information**:
   - App name: "Fall Detection Monitor"
   - Description: Include system features and benefits
   - Category: Medical/Health & Fitness
   - Screenshots: Capture key app screens
   - Privacy Policy: Create policy covering data collection

### Apple App Store

1. **Prepare iOS Build**:
   ```bash
   flutter build ios --release
   ```

2. **App Store Connect**:
   - Archive app in Xcode
   - Upload to App Store Connect
   - Create app listing
   - Submit for review

3. **Required Information**:
   - Similar to Google Play Store
   - Additional iOS-specific requirements

## 🔐 Security Configuration

### Firestore Security Rules
```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /fall_events/{document} {
      allow read, write: if request.auth != null; // Require authentication
    }
    match /devices/{document} {
      allow read, write: if request.auth != null;
    }
  }
}
```

### Network Security (Android)
Create `android/app/src/main/res/xml/network_security_config.xml`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <domain-config cleartextTrafficPermitted="false">
        <domain includeSubdomains="true">your-backend-domain.com</domain>
    </domain-config>
</network-security-config>
```

Update AndroidManifest.xml:
```xml
<application
    android:networkSecurityConfig="@xml/network_security_config">
```

## 🔧 Configuration Updates

### Update App Name and Package
1. **Android**: Update `android/app/src/main/AndroidManifest.xml`
2. **iOS**: Update in Xcode project settings
3. **pubspec.yaml**: Update app name and description

### Custom App Icons
```bash
# Install flutter_launcher_icons
flutter pub add dev:flutter_launcher_icons

# Add to pubspec.yaml:
flutter_icons:
  android: true
  ios: true
  image_path: "assets/icon/app_icon.png"

# Generate icons
flutter pub run flutter_launcher_icons:main
```

## 🧪 Testing

### Unit Tests
```bash
flutter test
```

### Integration Tests
```bash
flutter test integration_test/
```

### Test on Different Devices
- Test on various Android versions (API 21+)
- Test on different iOS versions (iOS 12+)
- Test notification functionality thoroughly

## 🔍 Troubleshooting

### Common Issues

1. **Firebase initialization fails**:
   ```dart
   // Ensure proper initialization in main.dart
   await Firebase.initializeApp();
   ```

2. **Notifications not working**:
   - Check FCM token generation
   - Verify notification permissions
   - Test with Firebase Console

3. **Build errors**:
   ```bash
   flutter clean
   flutter pub get
   flutter build apk --release
   ```

4. **iOS build issues**:
   - Update CocoaPods: `cd ios && pod install`
   - Clean Xcode build folder
   - Check minimum iOS version (11.0+)

### Performance Optimization

1. **Reduce APK size**:
   ```bash
   flutter build apk --release --split-per-abi
   ```

2. **Optimize images**:
   - Use appropriate image formats
   - Compress images
   - Use vector graphics where possible

## 📱 App Features Checklist

- [x] Real-time notification reception
- [x] Background notification handling
- [x] Fall event history display
- [x] Device statistics
- [x] Event acknowledgment
- [x] Firebase synchronization
- [x] Local notification display
- [x] Offline capability (partial)

## 🔄 Updates and Maintenance

### OTA Updates
- Implement code push for minor updates
- Use Firebase Remote Config for feature flags

### Monitoring
- Set up Firebase Crashlytics
- Monitor app performance
- Track user engagement

### Version Management
```yaml
# pubspec.yaml
version: 1.0.0+1  # version+build_number
```

## 📚 Additional Resources

- [Flutter Documentation](https://docs.flutter.dev/)
- [Firebase for Flutter](https://firebase.flutter.dev/)
- [Android Publishing Guide](https://developer.android.com/studio/publish)
- [iOS Publishing Guide](https://developer.apple.com/ios/submit/)
- [Flutter Performance Best Practices](https://docs.flutter.dev/perf/best-practices)

## 🆘 Support

For deployment issues:
1. Check Flutter doctor output
2. Verify Firebase configuration
3. Test on clean device/emulator
4. Review platform-specific documentation
5. Check error logs and stack traces