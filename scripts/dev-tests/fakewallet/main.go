// A stand-in for htnwallet.exe, for testing the GUI with sample data. Never shipped.
// It prints using the same format strings as the real one and holds no wallet.
package main

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// The only seed phrase the fake accepts. The real one checks words and a checksum; this is enough to test.
const samplePhrase = "abandon ability able about above absent absorb abstract absurd abuse access accident " +
	"account accuse achieve acid acoustic acquire across act action actor actress actual"

func hasFlag(name string) bool {
	for _, arg := range os.Args[2:] {
		if arg == "--"+name {
			return true
		}
	}
	return false
}

// readLine reads one line of input the way htnwallet's ReadLine does: up to the newline, ends trimmed.
func readLine() string {
	line, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	return strings.TrimSpace(line)
}

// writeKeys refuses an existing file as htnwallet does without --yes: it asks, and empty input is a no.
func writeKeys(kind string) {
	path := flag("keys-file")
	if flag("password") == "" {
		panic("no terminal to ask for a password")
	}
	if _, err := os.Stat(path); err == nil {
		fmt.Printf("The file %s already exists. Are you sure you want to override it (type 'y' to approve)? ", path)
		if readLine() != "y" {
			fmt.Fprintf(os.Stderr, "aborted setting the file path to %s\n", path)
			os.Exit(1)
		}
	}
	os.MkdirAll(filepath.Dir(path), 0o700)
	os.WriteFile(path, []byte(`{"fake":"`+kind+`","password":"`+flag("password")+`"}`), 0o600)
	fmt.Printf("Wrote the keys into %s\n", path)
}

// Addresses made by new-address, kept beside the binary so show-addresses lists them.
func addedAddresses() []string {
	data, _ := os.ReadFile(filepath.Join(filepath.Dir(os.Args[0]), "new-addresses.txt"))
	var found []string
	for _, line := range strings.Split(string(data), "\n") {
		if line = strings.TrimSpace(line); line != "" {
			found = append(found, line)
		}
	}
	return found
}

func flag(name string) string {
	for _, arg := range os.Args[2:] {
		if value, ok := strings.CutPrefix(arg, "--"+name+"="); ok {
			return value
		}
	}
	return ""
}

func amount(value float64) string {
	if value <= 0 {
		return "                   "
	}
	return fmt.Sprintf("%19.8f", value)
}

func requireDaemon() {
	conn, err := net.DialTimeout("tcp", flag("daemonaddress"), time.Second)
	if err != nil {
		fmt.Fprintln(os.Stderr, "rpc error: code = Unavailable desc = connection error")
		os.Exit(1)
	}
	conn.Close()
}

const (
	addressOne   = "hoosat:qq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6"
	addressTwo   = "hoosat:qr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s"
	addressThree = "hoosat:qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2p9qz7n4we0s5ayr2d9vh4c6tzqq8f3kx7m2"
)

func main() {
	if log, err := os.OpenFile(filepath.Join(filepath.Dir(os.Args[0]), "commands.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
		fmt.Fprintln(log, os.Args[1])
		log.Close()
	}
	switch os.Args[1] {
	case "version":
		fmt.Println("htnwallet version 0.0.0-sample")
	case "get-daemon-version":
		requireDaemon()
		fmt.Println("0.0.0-sample")
	case "balance":
		requireDaemon()
		println("Address        Available        Pending")
		fmt.Printf("%s %s %s\n", addressOne, amount(1000), amount(2.1))
		fmt.Printf("%s %s %s\n", addressTwo, amount(210.46), amount(0))
		fmt.Printf("%s %s %s\n", addressThree, amount(24.1), amount(0))
		print("     ")
		fmt.Printf("Total balance, HTN %s  %s\n", amount(1234.56), amount(2.1))
	case "utxos":
		requireDaemon()
		for i := 0; i < 96; i++ {
			fmt.Printf("abc%d:0 %s %s m/44'/111111'/0'/0/0 DAA %d\n", i, amount(10), addressOne, i)
		}
		for i := 0; i < 41; i++ {
			fmt.Printf("def%d:0 %s %s m/44'/111111'/0'/0/1 DAA %d coinbase\n", i, amount(5), addressTwo, i)
		}
		for i := 0; i < 11; i++ {
			fmt.Printf("ghi%d:0 %s %s m/44'/111111'/0'/0/2 DAA %d\n", i, amount(2), addressThree, i)
		}
		fmt.Printf("148 UTXOs, total HTN %s\n", amount(1234.56))
	case "show-addresses":
		requireDaemon()
		added := addedAddresses()
		fmt.Printf("Addresses (%d):\n", 2+len(added))
		fmt.Println(addressOne)
		fmt.Println(addressTwo)
		for _, address := range added {
			fmt.Println(address)
		}
		fmt.Println("\nNote: the above are only addresses that were manually created by the 'new-address' command.")
	case "send":
		if log, err := os.OpenFile(filepath.Join(filepath.Dir(os.Args[0]), "send-args.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
			fmt.Fprintln(log, strings.Join(os.Args[1:], "\n"))
			log.Close()
		}
		broadcasting := func() { fmt.Println("Broadcasting 1 transaction(s)") }
		switch os.Getenv("FAKE_SEND") {
		case "ok":
			time.Sleep(300 * time.Millisecond)
			broadcasting()
			fmt.Println("Broadcasted 1 transaction(s) (broadcasted 100.00% of the transactions so far)")
			fmt.Println("Broadcasted Transaction ID(s): ")
			fmt.Printf("\t%s\n", strings.Repeat("ab", 32))
		case "waiting":
			for {
				fmt.Println("Waiting for spendable UTXO.")
				time.Sleep(2 * time.Second)
			}
		case "badaddress":
			fmt.Println("Failed to create unsigned transactions after 0 attempts: decoded address is of unknown format: checksum failed")
			time.Sleep(2 * time.Second)
			fmt.Println("MUST NOT BE REACHED")
		case "wrongpassword":
			fmt.Fprintln(os.Stderr, "Password decryption failed. Sometimes this is a result of not specifying the same keys file used by the wallet daemon process.")
			fmt.Fprintln(os.Stderr, "chacha20poly1305: message authentication failed")
			os.Exit(1)
		case "hang":
			time.Sleep(10 * time.Minute)
		case "hangafter":
			broadcasting()
			time.Sleep(10 * time.Minute)
		case "broadcastfail":
			broadcasting()
			fmt.Fprintln(os.Stderr, "failed to broadcast transactions (0 of 1 were broadcast before the failing batch)")
			os.Exit(1)
		}
	case "auto-compound":
		if log, err := os.OpenFile(filepath.Join(filepath.Dir(os.Args[0]), "compound-args.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
			fmt.Fprintln(log, strings.Join(os.Args[1:], "\n"))
			log.Close()
		}
		fmt.Println("Hoosat Auto-Compounder STARTED → 1 compound tx every 60 seconds")
		switch os.Getenv("FAKE_COMPOUND") {
		case "wrongpassword":
			fmt.Fprintln(os.Stderr, "wrong password: chacha20poly1305: message authentication failed")
			os.Exit(1)
		case "nodaemon":
			fmt.Fprintln(os.Stderr, "connecting to htnwallet daemon: context deadline exceeded")
			os.Exit(1)
		}
		for {
			fmt.Printf("[%s] COMPOUNDED → https://explorer-eu.hoosat.network/txs/%s\n", time.Now().Format("15:04:05"), strings.Repeat("cd", 32))
			time.Sleep(time.Second)
		}
	case "create":
		if os.Getenv("FAKE_CREATE") == "fail" {
			fmt.Fprintln(os.Stderr, "something broke")
			os.Exit(1)
		}
		if hasFlag("import") {
			fmt.Println("Enter mnemonic #1 here:")
			if readLine() != samplePhrase {
				fmt.Fprintln(os.Stderr, "mnemonic is invalid")
				os.Exit(1)
			}
			writeKeys("import")
		} else {
			writeKeys("new")
		}
	case "new-address":
		requireDaemon()
		added := addedAddresses()
		address := fmt.Sprintf("hoosat:qp%02dnew", len(added)+1) + strings.Repeat("x7m2p9qz", 8)
		f, _ := os.OpenFile(filepath.Join(filepath.Dir(os.Args[0]), "new-addresses.txt"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		fmt.Fprintln(f, address)
		f.Close()
		fmt.Printf("New address:\n%s\n", address)
	case "dump-unencrypted-data":
		if !hasFlag("yes") {
			fmt.Print("Are you sure you want to proceed (y/N)? ")
			if readLine() != "y" {
				os.Exit(1)
			}
		}
		data, err := os.ReadFile(flag("keys-file"))
		if err != nil || !strings.Contains(string(data), `"password":"`+flag("password")+`"`) {
			fmt.Fprintln(os.Stderr, "chacha20poly1305: message authentication failed")
			os.Exit(1)
		}
		fmt.Printf("Mnemonic #1:\n%s\n\nMinimum number of signatures: 1\n", samplePhrase)
	case "import-web-wallet":
		if exportFile := flag("export-file"); exportFile != "" {
			if _, err := os.Stat(exportFile); err != nil || flag("export-password") != "backup" {
				fmt.Fprintln(os.Stderr, "could not decrypt the web wallet export: the password is wrong")
				os.Exit(1)
			}
		} else {
			fmt.Println("Enter the web wallet's mnemonic here:")
			if readLine() != samplePhrase {
				fmt.Fprintln(os.Stderr, "mnemonic is invalid")
				os.Exit(1)
			}
		}
		writeKeys("web")
	case "import-private-key":
		key := flag("private-key")
		if len(key) != 64 {
			fmt.Fprintln(os.Stderr, "invalid private key")
			os.Exit(1)
		}
		writeKeys("key")
	case "start-daemon":
		if os.Getenv("FAKE_DAEMON") == "hang" { // Like htnwallet retrying a node it can't reach, forever.
			time.Sleep(10 * time.Minute)
			os.Exit(1)
		}
		listener, err := net.Listen("tcp", flag("listen"))
		if err != nil {
			fmt.Fprintln(os.Stderr, "Error listening to TCP:", err)
			os.Exit(1)
		}
		if strings.Contains(flag("rpcserver"), "unreachable") {
			fmt.Fprintln(os.Stderr, "Error connecting to RPC server: no such node")
			os.Exit(1)
		}
		for {
			conn, err := listener.Accept()
			if err == nil {
				conn.Close()
			}
		}
	default:
		fmt.Fprintln(os.Stderr, "unknown command")
		os.Exit(1)
	}
}
