package api

import (
	"embed"
	"io/fs"
)

//go:embed web/*
var webRaw embed.FS

// WebFS returns an fs.FS rooted at the embedded web/ directory so that
// "index.html" resolves correctly when handed to http.FileServer.
func WebFS() fs.FS {
	sub, err := fs.Sub(webRaw, "web")
	if err != nil {
		// Build-time guarantee: web/ exists in the embed tree.
		panic(err)
	}
	return sub
}
