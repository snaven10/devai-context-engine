package main

import (
	"fmt"
	"os"

	"github.com/snaven10/devai/cmd/devai/cmd"
)

var (
	version = "dev"
	commit  = "none"
)

func main() {
	cmd.SetVersionInfo(version, commit)
	if err := cmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
