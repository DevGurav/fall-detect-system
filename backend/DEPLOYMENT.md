# Backend Deployment Guide

This guide covers deploying the Flask backend to various cloud platforms.

## 🚀 Render Deployment (Recommended)

### Step 1: Prepare Your Repository
1. Ensure your code is pushed to a GitHub repository
2. Make sure `requirements.txt` and `Procfile` are in the backend directory

### Step 2: Create Render Service
1. Go to [Render.com](https://render.com) and sign up
2. Click "New" → "Web Service"
3. Connect your GitHub repository
4. Configure the service:
   - **Environment**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn main:app`
   - **Root Directory**: `backend` (if backend is in a subdirectory)

### Step 3: Set Environment Variables
In the Render dashboard, add these environment variables:
- `FIREBASE_SERVICE_ACCOUNT_KEY`: Your Firebase service account JSON (as string)
- `FLASK_ENV`: `production`
- `SECRET_KEY`: A secure random string
- `PORT`: `5000` (optional, Render sets this automatically)

### Step 4: Deploy
1. Click "Create Web Service"
2. Render will automatically build and deploy your app
3. You'll get a URL like `https://your-app-name.onrender.com`

## 🚀 Railway Deployment

### Step 1: Install Railway CLI
```bash
npm install -g @railway/cli
```

### Step 2: Login and Initialize
```bash
railway login
cd backend
railway init
```

### Step 3: Set Environment Variables
```bash
railway variables set FIREBASE_SERVICE_ACCOUNT_KEY="your-json-key-here"
railway variables set FLASK_ENV=production
railway variables set SECRET_KEY="your-secret-key"
```

### Step 4: Deploy
```bash
railway up
```

## 🚀 Heroku Deployment

### Step 1: Install Heroku CLI
Download and install from [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli)

### Step 2: Create and Deploy
```bash
cd backend
heroku create your-app-name
heroku config:set FIREBASE_SERVICE_ACCOUNT_KEY="your-json-key-here"
heroku config:set FLASK_ENV=production
heroku config:set SECRET_KEY="your-secret-key"
git add .
git commit -m "Deploy to Heroku"
git push heroku main
```

## 🔧 Environment Variables Setup

### Firebase Service Account Key
1. Go to Firebase Console → Project Settings → Service Accounts
2. Click "Generate new private key"
3. Download the JSON file
4. Copy the entire JSON content as a string for the environment variable

Example:
```json
{
  "type": "service_account",
  "project_id": "your-project-id",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "...",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "..."
}
```

## 🛠️ Local Development

### Setup
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Firebase credentials
python main.py
```

### Testing API Endpoints
```bash
# Health check
curl https://your-app-url.com/health

# Test prediction (replace with your URL)
curl -X POST https://your-app-url.com/predict \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "test_device",
    "accelX": 0.1,
    "accelY": 0.2,
    "accelZ": 0.9,
    "gyroX": 0.0,
    "gyroY": 0.0,
    "gyroZ": 0.0
  }'
```

## 📊 Monitoring and Logs

### Render
- View logs in the Render dashboard
- Set up alerts for service health
- Monitor resource usage

### Railway
```bash
railway logs
```

### Heroku
```bash
heroku logs --tail
```

## 🔒 Security Best Practices

1. **Never commit sensitive data**:
   - Add `.env` to `.gitignore`
   - Use environment variables for all secrets

2. **Use HTTPS**:
   - All deployment platforms provide HTTPS by default
   - Ensure your ESP32 uses HTTPS endpoints

3. **Firebase Security**:
   - Set up proper Firestore security rules
   - Limit service account permissions
   - Regularly rotate service account keys

4. **API Security** (for production):
   ```python
   # Add to main.py for API key authentication
   from functools import wraps
   
   def require_api_key(f):
       @wraps(f)
       def decorated_function(*args, **kwargs):
           api_key = request.headers.get('X-API-Key')
           if api_key != os.getenv('API_KEY'):
               return jsonify({'error': 'Invalid API key'}), 401
           return f(*args, **kwargs)
       return decorated_function
   
   @app.route('/predict', methods=['POST'])
   @require_api_key
   def predict():
       # ... existing code
   ```

## 🚨 Troubleshooting

### Common Issues

1. **Import errors**:
   - Ensure all dependencies are in `requirements.txt`
   - Check Python version compatibility

2. **Firebase connection issues**:
   - Verify service account key format
   - Check Firebase project permissions
   - Ensure Firestore is enabled

3. **Memory issues**:
   - Optimize ML model loading
   - Consider model caching strategies

4. **Port binding issues**:
   ```python
   # Use environment PORT variable
   port = int(os.environ.get('PORT', 5000))
   app.run(host='0.0.0.0', port=port)
   ```

### Performance Optimization

1. **Model Loading**:
   ```python
   # Load model once at startup, not on each request
   fall_model = None
   
   def load_ml_model():
       global fall_model
       if fall_model is None:
           fall_model = joblib.load('fall_model.pkl')
   ```

2. **Database Connection Pooling**:
   - Firebase Admin SDK handles connection pooling automatically

3. **Caching**:
   ```python
   # Add caching for frequently accessed data
   from functools import lru_cache
   
   @lru_cache(maxsize=128)
   def get_device_tokens():
       # ... implementation
   ```

## 📈 Scaling Considerations

- **Horizontal Scaling**: Most platforms support automatic scaling
- **Database**: Firebase Firestore scales automatically
- **Monitoring**: Set up application performance monitoring
- **Load Balancing**: Handled by deployment platforms

## 🔄 CI/CD Pipeline

### GitHub Actions Example
Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Render

on:
  push:
    branches: [ main ]
    paths: [ 'backend/**' ]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    - name: Deploy to Render
      run: |
        curl -X POST "${{ secrets.RENDER_DEPLOY_HOOK }}"
```

## 📚 Additional Resources

- [Flask Documentation](https://flask.palletsprojects.com/)
- [Firebase Admin SDK](https://firebase.google.com/docs/admin/setup)
- [Render Documentation](https://render.com/docs)
- [Railway Documentation](https://docs.railway.app/)
- [Heroku Python Guide](https://devcenter.heroku.com/articles/getting-started-with-python)