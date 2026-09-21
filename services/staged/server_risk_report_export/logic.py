import sys
sys.path.insert(0, 'services/_exemplar')
from services._exemplar import logic as exemplar
import inspect
print(inspect.getsource(exemplar))