/**
 * Fix Discovery infinite loop bug
 * - Add tracking of seen brand IDs
 * - Fetch more brands when approaching end
 * - Pass exclude_ids to API to prevent duplicates
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Step 1: Add new state variables for tracking seen brands
const statePattern = /(\s+const \[revealingContact, setRevealingContact\] = useState\(false\);)/;
const newStates = `$1
  const [seenBrandIds, setSeenBrandIds] = useState(new Set()); // Track all brands shown to avoid duplicates
  const [fetchingMore, setFetchingMore] = useState(false);`;

content = content.replace(statePattern, newStates);

// Step 2: Update fetchBrands to accept excludeIds parameter and track seen brands
const fetchBrandsPattern = /const fetchBrands = async \(\) => \{[\s\S]*?try \{[\s\S]*?setLoading\(true\);/;
const newFetchBrands = `const fetchBrands = async (excludeIds = []) => {
    try {
      setLoading(true);`;

content = content.replace(fetchBrandsPattern, newFetchBrands);

// Step 3: Add exclude_ids parameter to API call
const apiCallPattern = /const response = await axios\.get\(`\$\{API_BASE\}\/api\/pr-crm\/brands\?limit=20`,/;
const newApiCall = `// Build query params with exclusions
      const excludeIdsParam = excludeIds.length > 0 ? \`&exclude_ids=\${excludeIds.join(',')}\` : '';
      const response = await axios.get(\`\${API_BASE}/api/pr-crm/brands?limit=20\${excludeIdsParam}\`,`;

content = content.replace(apiCallPattern, newApiCall);

// Step 4: Track seen brands when setting brands
const setBrandsPattern = /(setBrands\(parsedBrands\);)/;
const newSetBrands = `$1
        // Track these brand IDs as seen
        const newSeenIds = new Set([...Array.from(seenBrandIds), ...parsedBrands.map(b => b.id)]);
        setSeenBrandIds(newSeenIds);`;

content = content.replace(setBrandsPattern, newSetBrands);

// Step 5: Add effect to fetch more brands when approaching end
const useEffectPattern = /(useEffect\(\(\) => \{[\s\S]*?fetchSubscriptionStatus\(\);[\s\S]*?\}, \[\]\);)/;
const newEffect = `$1

  // Fetch more brands when nearing the end of current batch
  useEffect(() => {
    const fetchMoreIfNeeded = async () => {
      // When we're 3 brands away from the end and not already fetching
      if (currentIndex >= brands.length - 3 && !fetchingMore && brands.length > 0) {
        setFetchingMore(true);
        try {
          const excludeArray = Array.from(seenBrandIds);
          const response = await axios.get(\`\${API_BASE}/api/pr-crm/brands?limit=20&exclude_ids=\${excludeArray.join(',')}\`, {
            withCredentials: true
          });

          if (response.data.success && response.data.brands.length > 0) {
            const parsedBrands = response.data.brands.map(brand => ({
              ...brand,
              regions: typeof brand.regions === 'string' && brand.regions.startsWith('[')
                ? JSON.parse(brand.regions)
                : brand.regions,
              niches: typeof brand.niches === 'string' && brand.niches.startsWith('[')
                ? JSON.parse(brand.niches)
                : brand.niches,
            }));

            // Append new brands to existing list
            setBrands(prev => [...prev, ...parsedBrands]);

            // Track new brand IDs
            const newSeenIds = new Set([...Array.from(seenBrandIds), ...parsedBrands.map(b => b.id)]);
            setSeenBrandIds(newSeenIds);
          }
        } catch (error) {
          console.error('Error fetching more brands:', error);
        } finally {
          setFetchingMore(false);
        }
      }
    };

    fetchMoreIfNeeded();
  }, [currentIndex, brands.length, fetchingMore, seenBrandIds, API_BASE]);`;

content = content.replace(useEffectPattern, newEffect);

fs.writeFileSync(filePath, content, 'utf8');

console.log('✅ Fixed Discovery infinite loop bug!');
console.log('Changes:');
console.log('  - Added seenBrandIds tracking to prevent duplicates');
console.log('  - Added fetchingMore state to prevent concurrent fetches');
console.log('  - Updated fetchBrands() to accept and use exclude_ids parameter');
console.log('  - Added automatic prefetching when nearing end of current batch');
console.log('  - Brands now append to list instead of replacing');
console.log('\nRestart React dev server to see changes.');
