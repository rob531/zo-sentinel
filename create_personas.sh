#!/bin/bash
BASE=/home/workspace/zo_sentinel
ollama create zo-backend-coder  -f $BASE/Modelfile.backend  && echo '[OK] zo-backend-coder'
ollama create zo-frontend-coder -f $BASE/Modelfile.frontend && echo '[OK] zo-frontend-coder'
ollama create zo-mesh-architect -f $BASE/Modelfile.arch     && echo '[OK] zo-mesh-architect'
ollama create zo-auditor        -f $BASE/Modelfile.auditor  && echo '[OK] zo-auditor'
echo 'Done. Test: ollama run zo-backend-coder'