#!/usr/bin/env python3
"""
ESP32 Fall Detection Simulation Script
======================================

This script simulates an ESP32 wearable device sending accelerometer and gyroscope data
to the Fall Detection System backend. It can simulate:
1. Normal daily activities
2. Fall detection events  
3. Continuous monitoring scenarios

Usage:
    python esp32_simulation.py --mode [normal|fall|continuous]
    
Requirements:
    pip install requests
"""

import requests
import json
import time
import random
import argparse
import datetime
from typing import Dict, Any

class ESP32Simulator:
    def __init__(self, backend_url: str = "https://fall-detect-system.onrender.com", 
                 device_id: str = "ESP32_WRISTBAND_001"):
        self.backend_url = backend_url
        self.device_id = device_id
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
    
    def generate_normal_activity_data(self) -> Dict[str, Any]:
        """Generate sensor data for normal daily activities"""
        return {
            "device_id": self.device_id,
            "accelX": random.uniform(-0.8, 0.8),       # Normal walking range
            "accelY": random.uniform(-0.8, 0.8),       # Side-to-side movement
            "accelZ": random.uniform(9.2, 10.4),       # Gravity + slight movement
            "gyroX": random.uniform(-0.5, 0.5),        # Minimal rotation
            "gyroY": random.uniform(-0.5, 0.5),        # Normal hand movement
            "gyroZ": random.uniform(-0.5, 0.5)         # Slight wrist rotation
        }
    
    def generate_fall_event_data(self) -> Dict[str, Any]:
        """Generate sensor data simulating a fall event"""
        fall_scenarios = [
            {  # Forward fall
                "accelX": random.uniform(12.0, 18.0),
                "accelY": random.uniform(-4.0, 4.0),
                "accelZ": random.uniform(-2.0, 4.0),
                "gyroX": random.uniform(150.0, 200.0),
                "gyroY": random.uniform(-50.0, 50.0),
                "gyroZ": random.uniform(-100.0, 100.0)
            },
            {  # Backward fall
                "accelX": random.uniform(-18.0, -12.0),
                "accelY": random.uniform(-4.0, 4.0),
                "accelZ": random.uniform(-2.0, 4.0),
                "gyroX": random.uniform(-200.0, -150.0),
                "gyroY": random.uniform(-50.0, 50.0),
                "gyroZ": random.uniform(-100.0, 100.0)
            },
            {  # Side fall
                "accelX": random.uniform(-4.0, 4.0),
                "accelY": random.uniform(12.0, 18.0),
                "accelZ": random.uniform(-2.0, 4.0),
                "gyroX": random.uniform(-50.0, 50.0),
                "gyroY": random.uniform(150.0, 200.0),
                "gyroZ": random.uniform(-100.0, 100.0)
            }
        ]
        
        scenario = random.choice(fall_scenarios)
        return {
            "device_id": self.device_id,
            **scenario
        }
    
    def send_sensor_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Send sensor data to the backend API"""
        try:
            response = self.session.post(f"{self.backend_url}/predict", json=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e), "success": False}
    
    def check_backend_health(self) -> Dict[str, Any]:
        """Check if the backend is healthy and ready"""
        try:
            response = self.session.get(f"{self.backend_url}/health")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e), "success": False}
    
    def simulate_normal_activity(self, duration_minutes: int = 5):
        """Simulate normal daily activity for specified duration"""
        print(f"🚶 Starting normal activity simulation for {duration_minutes} minutes...")
        print(f"📡 Device ID: {self.device_id}")
        print(f"🎯 Backend: {self.backend_url}")
        print("-" * 60)
        
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        reading_count = 0
        fall_alerts = 0
        
        while time.time() < end_time:
            data = self.generate_normal_activity_data()
            result = self.send_sensor_data(data)
            reading_count += 1
            
            if result.get('success', True):  # Assuming success if no error field
                fall_detected = result.get('fall_detected', False)
                confidence = result.get('confidence', 0.0)
                
                status = "🚨 FALL ALERT!" if fall_detected else "✅ Normal"
                if fall_detected:
                    fall_alerts += 1
                
                print(f"Reading #{reading_count:3d}: {status} "
                      f"(Confidence: {confidence*100:.1f}%) "
                      f"Accel: ({data['accelX']:.2f}, {data['accelY']:.2f}, {data['accelZ']:.2f})")
                
            else:
                print(f"❌ Error sending data: {result.get('error', 'Unknown error')}")
            
            time.sleep(2)  # Send data every 2 seconds
        
        print("\n" + "="*60)
        print(f"📊 Normal Activity Simulation Complete!")
        print(f"⏱️  Duration: {duration_minutes} minutes")
        print(f"📈 Total readings: {reading_count}")
        print(f"🚨 False alarms: {fall_alerts}")
        print(f"✅ Accuracy: {((reading_count - fall_alerts) / reading_count * 100):.1f}%")
    
    def simulate_fall_event(self):
        """Simulate a single fall event"""
        print("🚨 Simulating FALL EVENT...")
        print(f"📡 Device ID: {self.device_id}")
        print(f"🎯 Backend: {self.backend_url}")
        print("-" * 60)
        
        # Send a few normal readings first
        print("📊 Sending baseline normal activity...")
        for i in range(3):
            data = self.generate_normal_activity_data()
            result = self.send_sensor_data(data)
            print(f"Baseline {i+1}: Normal activity sent")
            time.sleep(1)
        
        # Send fall event
        print("\n💥 FALL EVENT OCCURRING NOW!")
        fall_data = self.generate_fall_event_data()
        result = self.send_sensor_data(fall_data)
        
        if result.get('success', True):
            fall_detected = result.get('fall_detected', False)
            confidence = result.get('confidence', 0.0)
            event_id = result.get('event_id', 'N/A')
            timestamp = result.get('timestamp', datetime.datetime.now().isoformat())
            
            print(f"\n🔥 FALL DETECTION RESULT:")
            print(f"   Fall Detected: {'✅ YES' if fall_detected else '❌ NO'}")
            print(f"   Confidence: {confidence*100:.1f}%")
            print(f"   Event ID: {event_id}")
            print(f"   Timestamp: {timestamp}")
            print(f"   Sensor Data:")
            print(f"     Accel: ({fall_data['accelX']:.2f}, {fall_data['accelY']:.2f}, {fall_data['accelZ']:.2f})")
            print(f"     Gyro:  ({fall_data['gyroX']:.2f}, {fall_data['gyroY']:.2f}, {fall_data['gyroZ']:.2f})")
            
            if fall_detected:
                print("\n📱 Push notification should be sent to registered devices!")
                print("🏥 Emergency contacts should be alerted!")
            else:
                print("\n⚠️  Warning: Fall not detected by ML model!")
        else:
            print(f"❌ Error: {result.get('error', 'Unknown error')}")
    
    def simulate_continuous_monitoring(self, duration_minutes: int = 10, fall_probability: float = 0.02):
        """Simulate continuous monitoring with occasional fall events"""
        print(f"🔄 Starting continuous monitoring for {duration_minutes} minutes...")
        print(f"📡 Device ID: {self.device_id}")
        print(f"🎯 Backend: {self.backend_url}")
        print(f"🎲 Fall probability: {fall_probability*100:.1f}% per reading")
        print("-" * 60)
        
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        reading_count = 0
        total_falls = 0
        detected_falls = 0
        
        while time.time() < end_time:
            reading_count += 1
            
            # Randomly decide if this should be a fall event
            is_fall_event = random.random() < fall_probability
            
            if is_fall_event:
                data = self.generate_fall_event_data()
                total_falls += 1
                event_type = "🚨 FALL EVENT"
            else:
                data = self.generate_normal_activity_data()
                event_type = "📊 Normal"
            
            result = self.send_sensor_data(data)
            
            if result.get('success', True):
                fall_detected = result.get('fall_detected', False)
                confidence = result.get('confidence', 0.0)
                
                if fall_detected:
                    detected_falls += 1
                    status = "🔥 DETECTED"
                else:
                    status = "✅ Normal"
                
                print(f"Reading #{reading_count:3d}: {event_type} -> {status} "
                      f"(Confidence: {confidence*100:.1f}%)")
                
                if is_fall_event and fall_detected:
                    print("    ✅ True positive: Fall correctly detected!")
                elif is_fall_event and not fall_detected:
                    print("    ❌ False negative: Fall missed!")
                elif not is_fall_event and fall_detected:
                    print("    ⚠️  False positive: Normal activity flagged as fall!")
                
            else:
                print(f"❌ Error: {result.get('error', 'Unknown error')}")
            
            time.sleep(2)
        
        print("\n" + "="*60)
        print(f"📊 Continuous Monitoring Complete!")
        print(f"⏱️  Duration: {duration_minutes} minutes")
        print(f"📈 Total readings: {reading_count}")
        print(f"🚨 Actual falls: {total_falls}")
        print(f"🔥 Detected falls: {detected_falls}")
        if total_falls > 0:
            print(f"🎯 Detection rate: {(detected_falls / total_falls * 100):.1f}%")


def main():
    parser = argparse.ArgumentParser(description='ESP32 Fall Detection Simulator')
    parser.add_argument('--mode', choices=['normal', 'fall', 'continuous'], 
                       default='fall', help='Simulation mode')
    parser.add_argument('--duration', type=int, default=5, 
                       help='Duration in minutes (for normal/continuous modes)')
    parser.add_argument('--device-id', default='ESP32_WRISTBAND_001',
                       help='Device ID for simulation')
    parser.add_argument('--backend-url', default='https://fall-detect-system.onrender.com',
                       help='Backend API URL')
    parser.add_argument('--fall-probability', type=float, default=0.02,
                       help='Fall probability per reading (continuous mode)')
    
    args = parser.parse_args()
    
    simulator = ESP32Simulator(args.backend_url, args.device_id)
    
    # Check backend health first
    print("🏥 Checking backend health...")
    health = simulator.check_backend_health()
    if health.get('error'):
        print(f"❌ Backend health check failed: {health['error']}")
        print("⚠️  Simulation may fail. Please check your backend URL and connection.")
    else:
        print("✅ Backend is healthy and ready!")
        print(f"   Status: {health.get('status', 'unknown')}")
        print(f"   Firebase: {'✅' if health.get('firebase_connected') else '❌'}")
        print(f"   ML Model: {'✅' if health.get('model_loaded') else '❌'}")
    
    print("\n" + "="*60)
    
    # Run simulation based on mode
    if args.mode == 'normal':
        simulator.simulate_normal_activity(args.duration)
    elif args.mode == 'fall':
        simulator.simulate_fall_event()
    elif args.mode == 'continuous':
        simulator.simulate_continuous_monitoring(args.duration, args.fall_probability)


if __name__ == "__main__":
    main()