# Radio

Run:

    docker-compose up --build -d


debug:

    python3 -m venv env
    source env/bin/activate
    pip3 install -r requirements.txt
    
    export FLASK_ENV=development
    export FLASK_APP=app.py
    flask run --debug --port 8000
        
