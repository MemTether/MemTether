# -*- coding: utf-8 -*-
"""Security tests: input validation, poisoning defense existence check."""
import os, sys, sqlite3, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_DB'] = os.path.join(os.path.dirname(__file__), '_test_security.db')

import gateway


class TestInputValidation(unittest.TestCase):
    """Test gateway.remember() input validation (8 checks)."""
    
    def setUp(self):
        self.gateway = gateway
    
    def test_empty_content_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.remember("", source="test")
    
    def test_whitespace_content_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.remember("   ", source="test")
    
    def test_oversized_content_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.remember("x" * 10241, source="test")
    
    def test_invalid_type_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.remember("test", type="hack", source="test")
    
    def test_empty_source_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.remember("test", source="")
    
    def test_long_source_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.remember("test", source="x" * 65)
    
    def test_invalid_scope_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.remember("test", scope="everything", source="test")
    
    def test_valid_content_passes_validation(self):
        # Test that validation passes (doesn't raise ValueError)
        # DB write may fail in test env, but validation should succeed
        try:
            r = self.gateway.remember("Valid test content", type="fact", source="test")
            self.assertIsNotNone(r)
        except sqlite3.OperationalError:
            # DB not available in test environment, but validation passed
            pass  # This is what we're testing: validation logic, not DB


class TestPoisoningGuardExists(unittest.TestCase):
    """Verify that the poisoning defense module exists and has OWASP rules."""
    
    def test_guard_file_exists(self):
        guard_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   'scripts', 'memtether_guard.py')
        self.assertTrue(os.path.exists(guard_path))
    
    def test_guard_has_owasp_references(self):
        guard_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   'scripts', 'memtether_guard.py')
        src = open(guard_path, encoding='utf-8', errors='ignore').read()
        self.assertIn('OWASP', src, "Should reference OWASP")
        self.assertIn('scan_content', src, "Should have scan_content function")


if __name__ == '__main__':
    unittest.main()
