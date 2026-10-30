import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # prober/
sys.path.insert(0, HERE)                   # tests/
