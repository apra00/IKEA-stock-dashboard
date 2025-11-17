// ikea_stores.js
const checker = require('ikea-availability-checker');

(async () => {
  const args = process.argv.slice(2);
  const country = args[0];

  if (!country) {
    console.error("Usage: node ikea_stores.js <countryCode>");
    process.exit(1);
  }

  const stores = checker.stores.findByCountryCode(country);
  console.log(JSON.stringify(stores));
})().catch(err => {
  console.error(err);
  process.exit(1);
});
