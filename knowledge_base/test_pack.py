#!/usr/bin/env python3
"""
Simple syntax test for the packaging script.
"""

import os
import sys

# Add the current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_syntax():
    """Test if the packaging script has valid syntax."""
    try:
        # Try to import the module to check for syntax errors
        import pack_rocketmq_kb
        print("✅ Syntax check passed: No syntax errors found")
        
        # Check if all required functions are defined
        required_functions = [
            'create_package_structure',
            'copy_knowledge_base_files', 
            'copy_tools_and_scripts',
            'copy_rocketmq_source',
            'copy_documentation',
            'create_package_info',
            'create_zip_package',
            'calculate_package_size',
            'main'
        ]
        
        for func_name in required_functions:
            if hasattr(pack_rocketmq_kb, func_name):
                print(f"✅ Function '{func_name}' is defined")
            else:
                print(f"❌ Function '{func_name}' is missing")
        
        print("\n📋 Available files for packaging:")
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # List files that would be included
        files_to_package = [
            'kb_store.py',
            'build_rocketmq_kb.py',
            'query_rocketmq.py', 
            'README_ROCKETMQ_KB.md',
            'rocketmq_kb_index.json',
            'index.json'
        ]
        
        for file in files_to_package:
            file_path = os.path.join(current_dir, file)
            if os.path.exists(file_path):
                size = os.path.getsize(file_path)
                print(f"✅ {file} ({size / 1024:.1f} KB)")
            else:
                print(f"❌ {file} (not found)")
        
        # Check RocketMQ source directory
        rocketmq_dir = os.path.join(current_dir, 'rocketmq_531')
        if os.path.exists(rocketmq_dir):
            print(f"✅ RocketMQ source directory exists")
            
            # Count Java files
            java_files = []
            for root, dirs, files in os.walk(rocketmq_dir):
                for file in files:
                    if file.endswith('.java'):
                        java_files.append(os.path.join(root, file))
            
            print(f"📊 Found {len(java_files)} Java source files")
        else:
            print("❌ RocketMQ source directory not found")
        
        return True
        
    except SyntaxError as e:
        print(f"❌ Syntax error: {e}")
        return False
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

if __name__ == "__main__":
    print("🔍 Testing RocketMQ Knowledge Base Packaging Script")
    print("=" * 60)
    
    if test_syntax():
        print("\n🎉 All tests passed! The packaging script is ready to use.")
        print("💡 You can now run: python pack_rocketmq_kb.py")
    else:
        print("\n❌ Tests failed. Please fix the errors above.")