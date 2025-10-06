from flask import Flask
import logging
import subprocess

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Log messages
logging.info('app.py script started')

# Example 1: Running a simple command and capturing output
try:
    result = subprocess.run(['cron'], capture_output=True, text=True, check=True)
    print("STDOUT:")
    print(result.stdout)
    print("STDERR:")
    print(result.stderr)
except subprocess.CalledProcessError as e:
    print(f"Command failed with exit code {e.returncode}")
    print(f"STDOUT: {e.stdout}")
    print(f"STDERR: {e.stderr}")

app = Flask(__name__)

@app.route('/')
def hello():
    logging.warning('Hello, World! endpoint was reached')
    logging.info('INFO')
    logging.debug('debug')
    return "Radio to Spotify!!"

if __name__ == '__main__':
	app.run(host='0.0.0.0', port=8001)
	