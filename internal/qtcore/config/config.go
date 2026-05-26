package config

import (
	"bufio"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

type Config struct {
	ProjectRoot      string
	BackendDir       string
	DataDir          string
	WorkerScript     string
	PythonExecutable string
	DefaultHost      string
	DefaultPort      int
	Env              map[string]string
}

func Load(projectRoot string) (Config, error) {
	if strings.TrimSpace(projectRoot) == "" {
		wd, err := os.Getwd()
		if err != nil {
			return Config{}, err
		}
		projectRoot = wd
	}
	root, err := filepath.Abs(projectRoot)
	if err != nil {
		return Config{}, err
	}

	env := mergedEnv(root)
	backendDir := filepath.Join(root, "backend")
	dataDir := strings.TrimSpace(env["QUANT_DATA_DIR"])
	if dataDir == "" {
		dataDir = filepath.Join(backendDir, "data")
	} else if !filepath.IsAbs(dataDir) {
		dataDir = filepath.Join(root, dataDir)
	}

	return Config{
		ProjectRoot:      root,
		BackendDir:       backendDir,
		DataDir:          dataDir,
		WorkerScript:     filepath.Join(root, "scripts", "run_quant_job.py"),
		PythonExecutable: resolvePython(root, env),
		DefaultHost:      envString(env, "QT_GO_CONTROL_HOST", "127.0.0.1"),
		DefaultPort:      envInt(env, "QT_GO_CONTROL_PORT", 8090),
		Env:              env,
	}, nil
}

func (c Config) EnvList(extra map[string]string) []string {
	env := make(map[string]string, len(c.Env)+len(extra)+1)
	for key, value := range c.Env {
		env[key] = value
	}
	if c.BackendDir != "" {
		current := env["PYTHONPATH"]
		if current == "" {
			env["PYTHONPATH"] = c.BackendDir
		} else {
			env["PYTHONPATH"] = c.BackendDir + string(os.PathListSeparator) + current
		}
	}
	for key, value := range extra {
		env[key] = value
	}
	out := make([]string, 0, len(env))
	for key, value := range env {
		out = append(out, key+"="+value)
	}
	return out
}

func mergedEnv(projectRoot string) map[string]string {
	env := map[string]string{}
	for _, item := range os.Environ() {
		key, value, ok := strings.Cut(item, "=")
		if ok {
			env[key] = value
		}
	}
	for key, value := range readDotEnv(filepath.Join(projectRoot, ".env")) {
		if _, exists := env[key]; !exists {
			env[key] = value
		}
	}
	return env
}

func readDotEnv(path string) map[string]string {
	file, err := os.Open(path)
	if err != nil {
		return map[string]string{}
	}
	defer file.Close()

	env := map[string]string{}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		value = strings.TrimSpace(value)
		if key == "" {
			continue
		}
		if len(value) >= 2 {
			first := value[0]
			last := value[len(value)-1]
			if (first == '"' && last == '"') || (first == '\'' && last == '\'') {
				value = value[1 : len(value)-1]
			}
		}
		env[key] = value
	}
	return env
}

func resolvePython(projectRoot string, env map[string]string) string {
	if value := strings.TrimSpace(env["QT_PYTHON"]); value != "" {
		return value
	}
	candidates := []string{}
	if runtime.GOOS == "windows" {
		candidates = append(candidates, filepath.Join(projectRoot, ".venv", "Scripts", "python.exe"))
	} else {
		candidates = append(candidates, filepath.Join(projectRoot, ".venv", "bin", "python"))
	}
	for _, candidate := range candidates {
		if stat, err := os.Stat(candidate); err == nil && !stat.IsDir() {
			return candidate
		}
	}
	return "python"
}

func envString(env map[string]string, key string, fallback string) string {
	if value := strings.TrimSpace(env[key]); value != "" {
		return value
	}
	return fallback
}

func envInt(env map[string]string, key string, fallback int) int {
	value, err := strconv.Atoi(strings.TrimSpace(env[key]))
	if err != nil || value <= 0 {
		return fallback
	}
	return value
}
