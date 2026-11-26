import os
import sys
from pathlib import Path

# Keep test runs out of Application Insights. load_dotenv() does not override a variable
# that is already set, so the empty value wins over the key in .env.
os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
