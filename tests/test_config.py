import os
import stat
from unittest.mock import patch
import pytest

import config

def test_apply_to():
    class DummyApp:
        config = {}
    
    app = DummyApp()
    
    with patch.dict(os.environ, {'SESSION_COOKIE_SECURE': '1'}):
        config.apply_to(app)
        
    assert app.config['SESSION_COOKIE_SECURE'] is True
    assert app.config['SESSION_COOKIE_HTTPONLY'] is True

def test_load_secret_key_from_env():
    with patch.dict(os.environ, {'SECRET_KEY': 'env_secret'}):
        assert config.load_secret_key() == 'env_secret'

def test_load_secret_key_existing_file(tmp_path, monkeypatch):
    key_file = tmp_path / ".secret_key"
    key_file.write_text("file_secret")
    monkeypatch.setattr(config, 'SECRET_KEY_FILE', str(key_file))
    
    with patch.dict(os.environ, clear=True):
        assert config.load_secret_key() == 'file_secret'

def test_load_secret_key_creates_new(tmp_path, monkeypatch):
    key_file = tmp_path / ".secret_key"
    monkeypatch.setattr(config, 'SECRET_KEY_FILE', str(key_file))
    
    with patch.dict(os.environ, clear=True):
        new_key = config.load_secret_key()
        assert len(new_key) == 48 # 24 bytes hex
        assert key_file.read_text() == new_key

def test_load_secret_key_exception(monkeypatch):
    monkeypatch.setattr(config, 'SECRET_KEY_FILE', '/invalid/path/that/will/fail')
    
    with patch.dict(os.environ, clear=True):
        with patch('os.path.exists', side_effect=Exception("Test Error")):
            key = config.load_secret_key()
            assert len(key) == 48 # Fallback uses urandom

def test_harden_key_permissions(tmp_path):
    key_file = tmp_path / "test_key"
    key_file.write_text("test")
    os.chmod(str(key_file), 0o644)
    
    config._harden_key_permissions(str(key_file))
    
    if os.name != 'nt':
        assert os.stat(str(key_file)).st_mode & 0o077 == 0
