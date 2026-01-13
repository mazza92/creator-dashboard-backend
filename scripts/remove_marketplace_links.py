#!/usr/bin/env python3
"""
Script to remove all marketplace links from blog posts.
Reverts the changes made by the marketplace link addition scripts.
"""

import json
import re
from pathlib import Path

BLOG_POSTS_DIR = Path(__file__).parent.parent.parent / "creator-dashboard" / "src" / "content" / "posts"

def remove_marketplace_links(content):
    """Remove all marketplace links from content."""
    modified_content = content
    
    # Remove marketplace links with various patterns
    patterns_to_remove = [
        # Pattern: "Or <a href="/marketplace"...>browse our marketplace</a> first."
        r'\s*Or\s+<a href="/marketplace"[^>]*>browse our marketplace</a>\s*first\.',
        r'\s*Or\s+<a href="/marketplace"[^>]*>browse our marketplace</a>\.',
        r'\s*Or\s+<a href="/marketplace"[^>]*>browse our marketplace</a>',
        
        # Pattern: "<a href="/marketplace"...>Browse our marketplace</a> to see..."
        r'<a href="/marketplace"[^>]*>Browse our marketplace</a>\s+to see[^<]*\.',
        
        # Pattern: "<a href="/marketplace"...>Explore our marketplace</a> to filter..."
        r'<a href="/marketplace"[^>]*>Explore our marketplace</a>\s+to filter[^<]*\.',
        
        # Pattern: "Or <a href="/marketplace"...>browse our public marketplace</a> to discover..."
        r'\s*Or\s+<a href="/marketplace"[^>]*>browse our public marketplace</a>\s+to discover[^<]*\.',
        
        # Pattern: "<a href="/marketplace"...>Or browse our marketplace →</a>"
        r'<p[^>]*><a href="/marketplace"[^>]*>Or browse our marketplace →</a></p>',
        
        # Pattern: Any remaining marketplace links
        r'<a href="/marketplace"[^>]*>[^<]*</a>',
    ]
    
    for pattern in patterns_to_remove:
        modified_content = re.sub(pattern, '', modified_content, flags=re.IGNORECASE)
    
    # Clean up any leftover "Or" or "or" that might be orphaned
    modified_content = re.sub(r'\s+Or\s+\.', '.', modified_content)
    modified_content = re.sub(r'\s+or\s+\.', '.', modified_content)
    modified_content = re.sub(r'\.\s+Or\s+', '. ', modified_content)
    modified_content = re.sub(r'\.\s+or\s+', '. ', modified_content)
    
    # Clean up double spaces
    modified_content = re.sub(r'\s{2,}', ' ', modified_content)
    
    # Clean up broken HTML like "style="color: #26A69A; text-decoration: none; font-weight: 600;"> style="..."
    modified_content = re.sub(r'style="[^"]*">\s*style="[^"]*">', 'style="color: #26A69A; text-decoration: none; font-weight: 600;">', modified_content)
    
    # Remove any remaining broken style attributes in text
    modified_content = re.sub(r'\s+style="[^"]*">\s*', ' ', modified_content)
    
    return modified_content

def process_blog_post(file_path):
    """Process a single blog post JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            post = json.load(f)
        
        original_content = post.get('content', '')
        if not original_content or '/marketplace' not in original_content:
            return False
        
        modified_content = remove_marketplace_links(original_content)
        
        if modified_content != original_content:
            post['content'] = modified_content
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(post, f, indent=2, ensure_ascii=False)
            
            print(f"  ✓ Reverted {file_path.name}")
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
    
    print("Removing marketplace links from blog posts...\n")
    
    updated_count = 0
    total_count = 0
    
    for file_path in BLOG_POSTS_DIR.glob("*.json"):
        if file_path.name == "posts.json":
            continue
        
        total_count += 1
        if process_blog_post(file_path):
            updated_count += 1
    
    print(f"\n✓ Processed {total_count} blog posts")
    print(f"✓ Removed marketplace links from {updated_count} posts")

if __name__ == "__main__":
    main()

