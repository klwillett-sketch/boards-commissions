"""
DreamHost Passenger entry point.
This file tells DreamHost's Passenger server how to run the Flask app.
"""
import sys, os

# Add user-installed packages (pip install --user) to path
import site
sys.path.insert(0, site.getusersitepackages())

# Add the app directory to the path
cwd = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, cwd)

from app import app as application
