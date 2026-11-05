//go:build !windows

package main

func lockDir(string) (func(), error) { return func() {}, nil }
