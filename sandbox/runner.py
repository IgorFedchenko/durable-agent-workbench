"""Fixed container entrypoint. Untrusted code runs only inside Docker's boundary."""
import json
import os
import resource
import sys
import traceback

resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
os.umask(0o077)
try:
    source = open('/input/code.py', encoding='utf-8').read(12001)
    if len(source) > 12000:
        raise ValueError('source exceeds bound')
    scope = {'__name__': '__main__', 'DATASET': '/input/data.csv'}
    exec(compile(source, '/input/code.py', 'exec'), scope)
except BaseException:
    traceback.print_exc(limit=3)
    sys.exit(1)
