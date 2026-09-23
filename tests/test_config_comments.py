import tempfile
import unittest
from pathlib import Path
from controller.config import read_yaml, write_yaml


class CommentTests(unittest.TestCase):
    def test_comments_survive_save_without_changing_values(self):
        value={'version':2,'instances':[{'name':'app','schedule':{'start':{'enabled':False,'time':'09:00'}},
                'rollover':{'enabled':True,'period_minutes':54}}]}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'instances.yaml'
            write_yaml(path,value)
            self.assertEqual(read_yaml(path),value)
            self.assertIn('续杯周期',path.read_text(encoding='utf-8'))
            write_yaml(path,read_yaml(path))
            self.assertIn('每日执行时间',path.read_text(encoding='utf-8'))
