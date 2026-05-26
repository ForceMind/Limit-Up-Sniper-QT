package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"limit-up-sniper-qt/internal/qtcore/config"
	"limit-up-sniper-qt/internal/qtcore/httpapi"
	"limit-up-sniper-qt/internal/qtcore/orchestrator"
	"limit-up-sniper-qt/internal/qtcore/pythonworker"
	"limit-up-sniper-qt/internal/qtcore/users"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		printUsage()
		return nil
	}
	switch args[0] {
	case "serve":
		return serve(args[1:])
	case "status":
		return status(args[1:])
	case "startup":
		return startup(args[1:])
	case "run":
		return runJob(args[1:])
	case "users":
		return usersCommand(args[1:])
	case "help", "-h", "--help":
		printUsage()
		return nil
	default:
		return fmt.Errorf("unknown command: %s", args[0])
	}
}

func serve(args []string) error {
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	host := fs.String("host", "", "listen host")
	port := fs.Int("port", 0, "listen port")
	token := fs.String("token", "", "optional x-qt-control-token value")
	if err := fs.Parse(args); err != nil {
		return err
	}
	app, cfg, err := buildApp(*root)
	if err != nil {
		return err
	}
	listenHost := firstNonEmpty(*host, cfg.DefaultHost)
	listenPort := *port
	if listenPort <= 0 {
		listenPort = cfg.DefaultPort
	}
	controlToken := firstNonEmpty(*token, cfg.Env["QT_GO_CONTROL_TOKEN"])
	server := httpapi.Server{App: app, ControlToken: controlToken}
	addr := listenHost + ":" + strconv.Itoa(listenPort)
	fmt.Fprintf(os.Stderr, "qtctl serving on http://%s\n", addr)
	return http.ListenAndServe(addr, server.Handler())
}

func status(args []string) error {
	fs := flag.NewFlagSet("status", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	if err := fs.Parse(args); err != nil {
		return err
	}
	app, _, err := buildApp(*root)
	if err != nil {
		return err
	}
	payload, err := app.Status(context.Background())
	if err != nil {
		return err
	}
	return printJSON(payload)
}

func startup(args []string) error {
	fs := flag.NewFlagSet("startup", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	date := fs.String("date", "", "target date")
	startDate := fs.String("start-date", "", "replay/data start date")
	newsHours := fs.Int("news-hours", 24, "news lookback hours")
	newsPages := fs.Int("news-pages", 8, "news pages")
	aiItems := fs.Int("ai-items", 20, "AI analysis max items")
	marketCodes := fs.Int("market-codes", 200, "max market codes")
	notify := fs.Bool("notify", true, "send trade notifications")
	replay := fs.Bool("replay", false, "run strategy replay after daily startup")
	if err := fs.Parse(args); err != nil {
		return err
	}
	app, _, err := buildApp(*root)
	if err != nil {
		return err
	}
	result, err := app.Startup(context.Background(), orchestrator.StartupRequest{
		Date:              *date,
		StartDate:         *startDate,
		NewsHours:         *newsHours,
		NewsPages:         *newsPages,
		AIItems:           *aiItems,
		MarketCodes:       *marketCodes,
		Notify:            *notify,
		RunStrategyReplay: *replay,
	})
	printErr := printJSON(result)
	if err != nil {
		return err
	}
	return printErr
}

func runJob(args []string) error {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	job := fs.String("job", "", "job name")
	payloadJSON := fs.String("payload-json", "{}", "job payload JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	payload, err := parseMap(*payloadJSON)
	if err != nil {
		return err
	}
	app, _, err := buildApp(*root)
	if err != nil {
		return err
	}
	result, runErr := app.RunJob(context.Background(), *job, payload)
	printErr := printJSON(result)
	if runErr != nil {
		return runErr
	}
	return printErr
}

func usersCommand(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("users requires a subcommand: list, create, update, reset-password, ban, unban, delete")
	}
	switch args[0] {
	case "list":
		return usersList(args[1:])
	case "create":
		return usersCreate(args[1:])
	case "update":
		return usersUpdate(args[1:])
	case "reset-password":
		return usersResetPassword(args[1:])
	case "ban":
		return usersBan(args[1:], true)
	case "unban":
		return usersBan(args[1:], false)
	case "delete":
		return usersDelete(args[1:])
	default:
		return fmt.Errorf("unknown users subcommand: %s", args[0])
	}
}

func usersList(args []string) error {
	fs := flag.NewFlagSet("users list", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	if err := fs.Parse(args); err != nil {
		return err
	}
	app, _, err := buildApp(*root)
	if err != nil {
		return err
	}
	result, err := app.Users.ListFrontendUsers(context.Background())
	if err != nil {
		return err
	}
	return printJSON(result)
}

func usersCreate(args []string) error {
	fs := flag.NewFlagSet("users create", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	username := fs.String("username", "", "frontend username")
	password := fs.String("password", "", "frontend password")
	profileJSON := fs.String("profile-json", "{}", "profile JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	profile, err := parseMap(*profileJSON)
	if err != nil {
		return err
	}
	app, _, err := buildApp(*root)
	if err != nil {
		return err
	}
	user, err := app.Users.CreateFrontendUser(context.Background(), users.CreateRequest{
		Username: *username,
		Password: *password,
		Profile:  profile,
	})
	if err != nil {
		return err
	}
	return printJSON(map[string]any{"status": "ok", "user": user})
}

func usersUpdate(args []string) error {
	fs := flag.NewFlagSet("users update", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	username := fs.String("username", "", "frontend username")
	profileJSON := fs.String("profile-json", "{}", "profile JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	profile, err := parseMap(*profileJSON)
	if err != nil {
		return err
	}
	app, _, err := buildApp(*root)
	if err != nil {
		return err
	}
	user, err := app.Users.UpdateFrontendUser(context.Background(), *username, users.UpdateRequest{Profile: profile})
	if err != nil {
		return err
	}
	return printJSON(map[string]any{"status": "ok", "user": user})
}

func usersResetPassword(args []string) error {
	fs := flag.NewFlagSet("users reset-password", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	username := fs.String("username", "", "frontend username")
	password := fs.String("password", "", "new password")
	if err := fs.Parse(args); err != nil {
		return err
	}
	app, _, err := buildApp(*root)
	if err != nil {
		return err
	}
	user, err := app.Users.ResetFrontendPassword(context.Background(), *username, *password)
	if err != nil {
		return err
	}
	return printJSON(map[string]any{"status": "ok", "user": user})
}

func usersBan(args []string, disabled bool) error {
	fs := flag.NewFlagSet("users ban", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	username := fs.String("username", "", "frontend username")
	reason := fs.String("reason", "", "disable reason")
	if err := fs.Parse(args); err != nil {
		return err
	}
	app, _, err := buildApp(*root)
	if err != nil {
		return err
	}
	user, err := app.Users.SetFrontendDisabled(context.Background(), *username, disabled, *reason)
	if err != nil {
		return err
	}
	return printJSON(map[string]any{"status": "ok", "user": user})
}

func usersDelete(args []string) error {
	fs := flag.NewFlagSet("users delete", flag.ExitOnError)
	root := fs.String("root", "", "project root")
	username := fs.String("username", "", "frontend username")
	if err := fs.Parse(args); err != nil {
		return err
	}
	app, _, err := buildApp(*root)
	if err != nil {
		return err
	}
	if err := app.Users.DeleteFrontendUser(context.Background(), *username); err != nil {
		return err
	}
	return printJSON(map[string]any{"status": "ok", "deleted": *username})
}

func buildApp(root string) (*orchestrator.App, config.Config, error) {
	cfg, err := config.Load(root)
	if err != nil {
		return nil, config.Config{}, err
	}
	runner := pythonworker.Runner{Config: cfg}
	authStore := users.Store{Path: filepath.Join(cfg.DataDir, "auth.json")}
	app := orchestrator.New(cfg, runner, authStore)
	return app, cfg, nil
}

func parseMap(text string) (map[string]any, error) {
	text = strings.TrimSpace(text)
	if text == "" {
		return map[string]any{}, nil
	}
	var payload map[string]any
	decoder := json.NewDecoder(strings.NewReader(text))
	decoder.UseNumber()
	if err := decoder.Decode(&payload); err != nil {
		return nil, err
	}
	if payload == nil {
		payload = map[string]any{}
	}
	return payload, nil
}

func printJSON(payload any) error {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	return encoder.Encode(payload)
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func printUsage() {
	fmt.Fprintln(os.Stderr, `qtctl manages the Limit-Up Sniper control plane.

Usage:
  qtctl serve [-host 127.0.0.1] [-port 8090]
  qtctl status
  qtctl startup [-date YYYY-MM-DD] [-start-date YYYY-MM-DD] [-replay=false]
  qtctl run -job news_fetch -payload-json '{"hours":12}'
  qtctl users list
  qtctl users create -username trader -password secret123
  qtctl users update -username trader -profile-json '{"simulated_cash":50000}'
  qtctl users reset-password -username trader -password secret123
  qtctl users ban -username trader -reason "risk control"
  qtctl users unban -username trader
  qtctl users delete -username trader

Environment:
  QT_PYTHON              Python executable, defaults to .venv when present
  QT_GO_CONTROL_HOST     HTTP host, defaults to 127.0.0.1
  QT_GO_CONTROL_PORT     HTTP port, defaults to 8090
  QT_GO_CONTROL_TOKEN    Optional HTTP management token`)
}
