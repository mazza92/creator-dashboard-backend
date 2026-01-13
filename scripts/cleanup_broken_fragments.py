#!/usr/bin/env python3
"""
Script to clean up broken sentence fragments left after removing marketplace links.
"""

import json
import re
from pathlib import Path

BLOG_POSTS_DIR = Path(__file__).parent.parent.parent / "creator-dashboard" / "src" / "content" / "posts"

def cleanup_fragments(content):
    """Clean up broken sentence fragments."""
    modified_content = content
    
    # Fix: "with advanced filters. to see vetted creators..."
    modified_content = re.sub(
        r'with advanced filters\.\s+to see vetted creators[^<]*',
        r'with advanced filters',
        modified_content
    )
    
    # Fix: "Sign Up as a Brand</a>."
    modified_content = re.sub(
        r'Sign Up as a Brand</a>\.',
        r'Sign Up as a Brand</a>',
        modified_content
    )
    
    # Fix: "Join Newcollab</a> or to discover creators before signing up. to start receiving PR packages."
    modified_content = re.sub(
        r'Newcollab</a>\s+or\s+to discover creators before signing up\.\s+to start',
        r'Newcollab</a> to start',
        modified_content
    )
    
    # Fix: "See how brands like yours use Newcollab to scale their PR programs efficiently."
    # This should be part of the previous sentence
    modified_content = re.sub(
        r'ready for your brand\.\s+See how brands like yours use Newcollab to scale their PR programs efficiently\.',
        r'ready for your brand. See how brands like yours use Newcollab to scale their PR programs efficiently.',
        modified_content
    )
    
    # Clean up any remaining "or to" fragments
    modified_content = re.sub(r'\s+or\s+to\s+', ' ', modified_content)
    
    # Clean up double periods
    modified_content = re.sub(r'\.\s*\.', '.', modified_content)
    
    # Clean up orphaned periods after closing tags
    modified_content = re.sub(r'</a>\.\s*</div>', r'</a></div>', modified_content)
    
    return modified_content

def process_blog_post(file_path):
    """Process a single blog post JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            post = json.load(f)
        
        original_content = post.get('content', '')
        if not original_content:
            return False
        
        modified_content = cleanup_fragments(original_content)
        
        if modified_content != original_content:
            post['content'] = modified_content
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(post, f, indent=2, ensure_ascii=False)
            
            print(f"  ✓ Cleaned {file_path.name}")
            return True
        
        return False
            
    except Exception as e:
        print(f"  ✗ Error processing {file_path.name}: {e}")
        return False

def main():
    """Main function."""
    if not BLOG_POSTS_DIR.exists():
        print(f"Error: Blog posts directory not found: {BLOG_POSTS_DIR}")
        return
    
    print("Cleaning up broken fragments in blog posts...\n")
    
    updated_count = 0
    total_count = 0
    
    for file_path in BLOG_POSTS_DIR.glob("*.json"):
        if file_path.name == "posts.json":
            continue
        
        total_count += 1
        if process_blog_post(file_path):
            updated_count += 1
    
    print(f"\n✓ Processed {total_count} blog posts")
    print(f"✓ Cleaned {updated_count} posts")

if __name__ == "__main__":
    main()

