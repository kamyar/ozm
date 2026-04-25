const name: string = Deno.env.get("USER") ?? "world";

interface Greeting {
  message: string;
  count: number;
}

function greet(name: string, times: number): Greeting[] {
  return Array.from({ length: times }, (_, i) => ({
    message: `hello ${name}!`,
    count: i + 1,
  }));
}

for (const g of greet(name, 3)) {
  console.log(`${g.message} (${g.count})`);
}
