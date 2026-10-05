# memtether-js

Thin Node.js wrapper for MemTether CLI.

```js
const { remember, search } = require("memtether-js");
remember("User prefers dark theme", { source: "my-app" });
const results = search("theme");
```
