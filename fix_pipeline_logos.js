const fs = require('fs');

const filePath = 'c:/Users/maher/Desktop/creator-dashboard/src/creator-portal/PRPipeline.js';

let content = fs.readFileSync(filePath, 'utf-8');

// Add a LogoContainer and LogoPlaceholder styled components after BrandLogo
const brandLogoSection = `const BrandLogo = styled.img\`
  width: 60px;
  height: 60px;
  border-radius: 12px;
  object-fit: contain;
  background: white;
  padding: 8px;
  border: 1px solid #E5E7EB;

  @media (max-width: 768px) {`;

const updatedSection = `const LogoContainer = styled.div\`
  position: relative;
  width: 60px;
  height: 60px;
  min-width: 60px;
  border-radius: 12px;
  background: white;
  border: 1px solid #E5E7EB;
  overflow: hidden;
\`;

const BrandLogo = styled.img\`
  width: 100%;
  height: 100%;
  object-fit: contain;
  padding: 8px;
\`;

const LogoPlaceholder = styled.div\`
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  font-size: 24px;
  font-weight: 700;
\`;

const BrandLogoStyled = styled.img\`
  width: 60px;
  height: 60px;
  border-radius: 12px;
  object-fit: contain;
  background: white;
  padding: 8px;
  border: 1px solid #E5E7EB;

  @media (max-width: 768px) {`;

content = content.replace(brandLogoSection, updatedSection);

// Replace the BrandLogo usage with the new container + error handling
const oldLogoUsage = `<BrandLogo src={getBrandLogoUrl(brand)} />`;

const newLogoUsage = `<LogoContainer>
                    <BrandLogo
                      src={getBrandLogoUrl(brand)}
                      onError={(e) => {
                        e.target.style.display = 'none';
                        const placeholder = e.target.nextSibling;
                        if (placeholder) placeholder.style.display = 'flex';
                      }}
                    />
                    <LogoPlaceholder style={{ display: 'none' }}>
                      {brand.brand_name.charAt(0).toUpperCase()}
                    </LogoPlaceholder>
                  </LogoContainer>`;

content = content.replace(oldLogoUsage, newLogoUsage);

fs.writeFileSync(filePath, content, 'utf-8');

console.log('[OK] Fixed Pipeline logo display with fallback placeholders');
console.log('- Added LogoContainer for proper layout');
console.log('- Added LogoPlaceholder for failed images');
console.log('- Shows brand initial when logo fails to load');
