#!/bin/sh
# Process-level Gradle stand-in: execute a controlled unittest suite and write
# its actual outcomes as JUnit XML. Never downloads a toolchain or contacts APIs.
case "$*" in
  *assembleDebug*)
    mkdir -p app/build/outputs/apk/debug
    printf 'PK\003\004CONTROLLED-APK' > app/build/outputs/apk/debug/app-debug.apk
    echo 'BUILD SUCCESSFUL in 1s'
    exit 0
    ;;
esac
exec python3 - <<'PY'
from pathlib import Path
import sys
import time
import unittest
import xml.etree.ElementTree as ET
mode = Path('delivery-mode.txt').read_text().strip()
root = Path('app/build')
root.mkdir(exist_ok=True)
(root / 'test-started').write_text(mode)
if mode == 'canceled':
    time.sleep(120)
if mode == 'missing_report':
    print('BUILD SUCCESSFUL in 1s', flush=True)
    sys.exit(0)
class Fixture(unittest.TestCase):
    def test_first(self):
        if mode == 'skipped': self.skipTest('controlled skip')
        self.assertEqual(2 + 2, 4)
    def test_second(self):
        if mode == 'skipped': self.skipTest('controlled skip')
        self.assertEqual(2 + 2, 5 if mode == 'failed' else 4)
    @unittest.skip('controlled optional case')
    def test_optional(self):
        pass
suite = unittest.TestSuite() if mode == 'zero' else unittest.defaultTestLoader.loadTestsFromTestCase(Fixture)
cases = list(suite)
result = unittest.TestResult()
suite.run(result)
report = ET.Element('testsuite', tests=str(result.testsRun), failures=str(len(result.failures)), errors=str(len(result.errors)), skipped=str(len(result.skipped)))
failures = dict(result.failures + result.errors)
skipped = dict(result.skipped)
for case in cases:
    node = ET.SubElement(report, 'testcase', name=case._testMethodName, classname='Fixture')
    if case in failures: ET.SubElement(node, 'failure', message='controlled assertion failure').text = failures[case]
    if case in skipped: ET.SubElement(node, 'skipped', message=skipped[case])
output = root / 'test-results/testDebugUnitTest/TEST-Fixture.xml'
output.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(report).write(output, encoding='utf-8', xml_declaration=True)
print('BUILD SUCCESSFUL in 1s' if result.wasSuccessful() else 'BUILD FAILED in 1s', flush=True)
sys.exit(0 if result.wasSuccessful() else 1)
PY
