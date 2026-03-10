rm -rf  ~/.nanobot/workspace/knowledge/init_status.json
rm -rf ~/.nanobot/workspace/knowledge/chroma_db 
echo 'clean chroma_db done'

python3 init.py

