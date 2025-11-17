// ikea_client.js
const checker = require('ikea-availability-checker');

(async () => {
  const args = process.argv.slice(2);
  const country = args[0];
  const productId = args[1];
  const storeIdsArg = args[2]; // comma separated or empty

  if (!country || !productId) {
    console.error("Usage: node ikea_client.js <country> <productId> [storeIds]");
    process.exit(1);
  }

  let stores = [];
  if (storeIdsArg && storeIdsArg.trim().length > 0) {
    const allStores = checker.stores.findByCountryCode(country);
    const wanted = storeIdsArg.split(',').map(s => s.trim());
    stores = allStores.filter(s => wanted.includes(String(s.buCode)));
  } else {
    stores = checker.stores.findByCountryCode(country);
  }

  const result = await checker.availabilities(stores, [productId]);
  console.log(JSON.stringify(result));
})().catch(err => {
  console.error(err);
  process.exit(1);
});
