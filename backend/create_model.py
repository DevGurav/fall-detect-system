import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

def create_fall_detection_model():
    """
    Creates a simple fall detection model using synthetic data.
    In production, you would train this with real fall detection data.
    """
    print("Creating fall detection model...")
    
    # Generate synthetic training data
    # Features: accelX, accelY, accelZ, gyroX, gyroY, gyroZ
    
    # Normal activity data (walking, sitting, standing)
    np.random.seed(42)
    normal_data = []
    
    # Walking: moderate acceleration, low gyroscope
    for _ in range(800):
        normal_data.append([
            np.random.normal(0.1, 0.3),    # accelX
            np.random.normal(0.1, 0.3),    # accelY  
            np.random.normal(9.8, 1.0),    # accelZ (gravity)
            np.random.normal(0.0, 0.2),    # gyroX
            np.random.normal(0.0, 0.2),    # gyroY
            np.random.normal(0.0, 0.2),    # gyroZ
        ])
    
    # Fall data (sudden acceleration changes, high rotation)
    fall_data = []
    for _ in range(200):
        fall_data.append([
            np.random.normal(0.0, 8.0),    # accelX (high variance)
            np.random.normal(0.0, 8.0),    # accelY (high variance)
            np.random.normal(2.0, 6.0),    # accelZ (low, impact)
            np.random.normal(0.0, 3.0),    # gyroX (rotation)
            np.random.normal(0.0, 3.0),    # gyroY (rotation)
            np.random.normal(0.0, 3.0),    # gyroZ (rotation)
        ])
    
    # Combine data
    X = np.array(normal_data + fall_data)
    y = np.array([0] * len(normal_data) + [1] * len(fall_data))  # 0 = normal, 1 = fall
    
    # Split data
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Create and train model
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        random_state=42,
        class_weight='balanced'  # Handle imbalanced data
    )
    
    print("Training model...")
    model.fit(X_train, y_train)
    
    # Evaluate model
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    
    print(f"Model accuracy: {accuracy:.2f}")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=['Normal', 'Fall']))
    
    # Feature importance
    features = ['accelX', 'accelY', 'accelZ', 'gyroX', 'gyroY', 'gyroZ']
    importance = model.feature_importances_
    
    print("\nFeature Importance:")
    for feature, imp in zip(features, importance):
        print(f"{feature}: {imp:.3f}")
    
    return model

if __name__ == "__main__":
    # Create the model
    fall_model = create_fall_detection_model()
    
    # Save the model
    joblib.dump(fall_model, 'fall_model.pkl')
    print("\nModel saved as 'fall_model.pkl'")
    
    # Test the model with sample data
    print("\n=== Testing Model ===")
    
    # Test normal activity
    normal_sample = [[0.1, 0.2, 9.8, 0.1, 0.0, 0.1]]
    normal_pred = fall_model.predict(normal_sample)[0]
    normal_prob = fall_model.predict_proba(normal_sample)[0]
    print(f"Normal activity prediction: {normal_pred} (probabilities: {normal_prob})")
    
    # Test fall activity
    fall_sample = [[5.0, -3.0, 1.0, 2.5, -1.8, 3.2]]
    fall_pred = fall_model.predict(fall_sample)[0]
    fall_prob = fall_model.predict_proba(fall_sample)[0]
    print(f"Fall activity prediction: {fall_pred} (probabilities: {fall_prob})")