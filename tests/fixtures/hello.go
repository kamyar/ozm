package main

import (
	"fmt"
	"os"
	"strings"
)

func main() {
	name := os.Getenv("USER")
	if name == "" {
		name = "world"
	}
	greeting := fmt.Sprintf("hello %s!", strings.Title(name))
	fmt.Println(greeting)
}
