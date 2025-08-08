from app import app

# This file is required for deployment platforms like Replit
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)