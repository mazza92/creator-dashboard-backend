/**
 * Fix logo loading issues
 *
 * Problems:
 * 1. Instagram CDN blocked by CORS
 * 2. Clearbit API fails for some domains
 *
 * Solution:
 * - Don't use Instagram profile pics (they expire and CORS blocks them)
 * - Use Clearbit as primary source
 * - Add better fallback with brand initials
 * - Hide broken image icons completely
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Update getBrandLogoUrl function to not use Instagram profile pics
const oldGetBrandLogoUrl = /\/\/ Utility function to get brand logo URL[\s\S]*?const getBrandLogoUrl = \(brand\) => \{[\s\S]*?\n\};/;

const newGetBrandLogoUrl = `// Utility function to get brand logo URL (for small icons)
const getBrandLogoUrl = (brand) => {
  // Don't use Instagram CDN (CORS blocked)
  // Only try Clearbit Logo API
  if (brand.website) {
    try {
      const url = new URL(brand.website.startsWith('http') ? brand.website : \`https://\${brand.website}\`);
      const domain = url.hostname.replace('www.', '');
      return \`https://logo.clearbit.com/\${domain}\`;
    } catch (e) {
      return null;
    }
  }

  return null;
};`;

content = content.replace(oldGetBrandLogoUrl, newGetBrandLogoUrl);

// Update BrandLogo styled component to hide on error better
const brandLogoPattern = /const BrandLogo = styled\.img`[\s\S]*?`;/;

const newBrandLogo = `const BrandLogo = styled.img\`
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;

  /* Hide completely on error */
  &[src=""], &:not([src]) {
    display: none;
  }
\`;`;

content = content.replace(brandLogoPattern, newBrandLogo);

// Update LogoContainer in the card to handle errors better
const logoContainerJSX = /<LogoContainer>[\s\S]*?<BrandLogo[\s\S]*?<\/LogoContainer>/;

const newLogoContainerJSX = `<LogoContainer>
                  <BrandLogo
                    src={getBrandLogoUrl(currentBrand)}
                    alt=""
                    onError={(e) => {
                      e.target.style.display = 'none';
                      const placeholder = e.target.nextElementSibling;
                      if (placeholder) placeholder.style.display = 'flex';
                    }}
                  />
                  <LogoPlaceholder style={{ display: getBrandLogoUrl(currentBrand) ? 'none' : 'flex' }}>
                    {currentBrand.brand_name.charAt(0).toUpperCase()}
                  </LogoPlaceholder>
                </LogoContainer>`;

content = content.replace(logoContainerJSX, newLogoContainerJSX);

fs.writeFileSync(filePath, content, 'utf8');

console.log('[OK] Fixed logo loading issues!');
console.log('\nChanges:');
console.log('  1. Removed Instagram CDN URLs (CORS blocked)');
console.log('  2. Only use Clearbit API for logos');
console.log('  3. Better fallback to brand initials');
console.log('  4. Hide broken image icons completely');
console.log('  5. Show placeholder immediately if no logo URL');
console.log('\nRestart React server to see changes.');
