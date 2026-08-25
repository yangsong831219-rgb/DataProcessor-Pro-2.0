"""Wrapper: set DEEPSEEK_API_KEY from config, then run forensic test."""
import json, os, sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent

# Read API key from config file (same source as GUI)
cfg_path = _project_root / "ai_models_config.json"
with open(cfg_path, encoding='utf-8') as f:
    cfg = json.load(f)

# The config has "deepseek V4 Pro" key with api_key
for key in cfg:
    if isinstance(cfg[key], dict) and 'api_key' in cfg[key]:
        api_key = cfg[key]['api_key']
        if api_key and api_key not in ('not-needed', ''):
            os.environ['DEEPSEEK_API_KEY'] = api_key
            break

# Run the forensic test
sys.path.insert(0, str(_project_root))
sys.argv = [sys.argv[0]] + sys.argv[1:]  # pass through args
exec(open(_project_root / "tests" / "forensic_slide_intent_test.py", encoding='utf-8').read())
