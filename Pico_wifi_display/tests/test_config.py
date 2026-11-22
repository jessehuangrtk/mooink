import copy
import json
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"pc"))
from generate_config import generate,literal
class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/"config.example.json").read_text())
        self.config["wifi"]["password"]="abcdefgh"
        self.config["smb"]["password"]="example-only"
    def test_private_octals(self):
        result=generate(self.config)
        self.assertNotIn("abcdefgh",result)
        self.assertIn("#define DISPLAY_POLL_SECONDS 60u",result)
        self.assertEqual(literal(b'"\n\\'),'"\\042\\012\\134"')
    def test_signing_required(self):
        self.config["smb"]["require_signing"]=False
        with self.assertRaises(ValueError):generate(self.config)
    def test_filename_fixed(self):
        self.config["smb"]["file"]="other.png"
        with self.assertRaises(ValueError):generate(self.config)
    def test_invalid_interval(self):
        self.config["poll_seconds"]=True
        with self.assertRaises(ValueError):generate(self.config)

    def test_debug_mode(self):
        self.assertIn("#define DISPLAY_DEBUG_MODE 0",generate(self.config))
        self.config["debug_mode"]=True
        self.assertIn("#define DISPLAY_DEBUG_MODE 1",generate(self.config))
        self.config["debug_mode"]="true"
        with self.assertRaises(ValueError):generate(self.config)
