from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess,sys,shutil,unittest

SOURCE=Path(__file__).resolve().parent

class InstallerTests(unittest.TestCase):
    def test_installer_fails_closed_if_old_code_unknown(self):
        with TemporaryDirectory() as t:
            root=Path(t)
            for name in ('install_v061u.py','v061u_analysis_ui.py','v061u_full_field.py'):
                shutil.copyfile(SOURCE/name,root/name)
            (root/'app.py').write_text('def analysis_tab():\n    pass\n')
            (root/'v043_full_field.py').write_text('def render():\n    pass\n')
            before=(root/'app.py').read_text()
            result=subprocess.run([sys.executable,str(root/'install_v061u.py')],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((root/'app.py').read_text(),before)
    def test_installer_replaces_ui_and_v043_idempotently(self):
        with TemporaryDirectory() as t:
            root=Path(t)
            for name in ('install_v061u.py','v061u_analysis_ui.py','v061u_full_field.py'):
                shutil.copyfile(SOURCE/name,root/name)
            (root/'app.py').write_text("def analysis_tab():\n    v061t_missing_data_ui.render_ranking_table(display_df, analysis['race_data'])\n    v043_full_field.render(db_rows)\n")
            (root/'v043_full_field.py').write_text("""def render(db_rows):
    try:
        predictions = db_rows('race_predictions', order='predicted_at')
        results = db_rows('runner_results', order=None)
    except Exception:
        pass
        summary.append({'Date': race[0], 'Track': official[0]['track'], 'Race': race[2],
                    'other': 1})
    st.subheader('Race-by-race performance')
""")
            results=[]
            for i in range(2):
                process=subprocess.run([sys.executable,str(root/'install_v061u.py')],capture_output=True,text=True)
                self.assertEqual(process.returncode,0,process.stderr+process.stdout)
                results.append(((root/'app.py').read_text(),(root/'v043_full_field.py').read_text()))
            self.assertEqual(results[0],results[1])
            self.assertIn('v061u_analysis_ui.render(display_df',results[0][0])
            self.assertIn('Schedule evidence',results[0][1])
            self.assertIn('prestart=s[',results[0][1])

if __name__=='__main__':unittest.main()
