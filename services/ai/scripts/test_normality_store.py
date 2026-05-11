import unittest
import os
import json
import shutil
import tempfile

from src.logic.normality_store import NormalityStore

class TestNormalityStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.persist_path = os.path.join(self.temp_dir, "profiles.json")
        self.store = NormalityStore(persist_path=self.persist_path, ema_alpha=0.1)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_critical_labels_ignored(self):
        # critical label should NOT be learned
        self.store.update_baseline("cam1", "audio_gunshot", 0.9)
        self.store.force_save()
        
        # adjusted score should be exactly raw_score
        adj, m, s, z = self.store.get_adjusted_score("cam1", "audio_gunshot", 0.85)
        self.assertEqual(adj, 0.85)
        self.assertEqual(m, 0.0)
        
        with open(self.persist_path, "r") as f:
            data = json.load(f)
            self.assertNotIn("cam1", data)

    def test_background_labels_learned(self):
        # feed 15 observations of engine noise
        for _ in range(15):
            self.store.update_baseline("cam1", "audio_engine", 0.6)
            
        adj, m, s, z = self.store.get_adjusted_score("cam1", "audio_engine", 0.6)
        
        self.assertGreater(m, 0.0)
        self.assertLess(adj, 0.6)
        self.assertEqual(adj, 0.0) # completely suppressed if equal to mean
        
        # Test a spike
        adj_spike, m2, s2, z2 = self.store.get_adjusted_score("cam1", "audio_engine", 0.9)
        self.assertGreater(adj_spike, 0.0)

    def test_persistence(self):
        for _ in range(15):
            self.store.update_baseline("cam1", "audio_train", 0.7)
            
        self.store.force_save()
        
        # Create new store instance pointing to same file
        store2 = NormalityStore(persist_path=self.persist_path)
        adj, m, s, z = store2.get_adjusted_score("cam1", "audio_train", 0.7)
        self.assertGreater(m, 0.0)

if __name__ == "__main__":
    unittest.main()
