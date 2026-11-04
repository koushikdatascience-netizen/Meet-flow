/* Shared, deterministic ranking for instant product lookup. */
(function (root) {
  const normalize = value => String(value).normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase().trim().replace(/\s+/g, ' ');
  function rank(products, query, allowUnavailable = false) {
    const term = normalize(query), tokens = term.split(' ').filter(Boolean);
    return products.map(product => {
      const name = normalize(product.name), words = name.split(' ');
      const matches = tokens.every(token => name.includes(token));
      let score = name === term ? 0 : name.startsWith(term) ? 1 : tokens.every(token => words.some(word => word.startsWith(token))) ? 2 : 3;
      return { product, matches, score, unavailable: !allowUnavailable && Number(product.stock) <= 0 };
    }).filter(item => item.matches).sort((a, b) => Number(a.unavailable) - Number(b.unavailable) || a.score - b.score || a.product.name.localeCompare(b.product.name)).map(item => item.product);
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { rank, normalize };
  else root.MeatFlowSearch = { rank, normalize };
})(typeof window !== 'undefined' ? window : this);
