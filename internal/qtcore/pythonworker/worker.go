package pythonworker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"

	"limit-up-sniper-qt/internal/qtcore/config"
)

type Runner struct {
	Config config.Config
}

func (r Runner) Run(ctx context.Context, job string, payload map[string]any) (map[string]any, error) {
	job = strings.TrimSpace(job)
	if job == "" {
		return nil, errors.New("job is required")
	}
	if payload == nil {
		payload = map[string]any{}
	}
	rawPayload, err := json.Marshal(map[string]any{"payload": payload})
	if err != nil {
		return nil, err
	}

	cmd := exec.CommandContext(
		ctx,
		r.Config.PythonExecutable,
		r.Config.WorkerScript,
		"--job",
		job,
		"--payload-json",
		string(rawPayload),
		"--emit-json",
	)
	cmd.Dir = r.Config.ProjectRoot
	cmd.Env = r.Config.EnvList(nil)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	runErr := cmd.Run()
	result := parseWorkerResult(job, stdout.String())
	if runErr != nil {
		result["status"] = "error"
		result["job"] = job
		result["message"] = runErr.Error()
		if text := strings.TrimSpace(stderr.String()); text != "" {
			result["stderr"] = text
		}
		if exitErr := new(exec.ExitError); errors.As(runErr, &exitErr) {
			result["exit_code"] = exitCode(exitErr)
		}
		return result, runErr
	}
	if _, ok := result["status"]; !ok {
		result["status"] = "ok"
	}
	result["job"] = job
	return result, nil
}

func parseWorkerResult(job string, output string) map[string]any {
	text := strings.TrimSpace(output)
	if text == "" {
		return map[string]any{"job": job}
	}
	var payload map[string]any
	if err := json.Unmarshal([]byte(text), &payload); err == nil && payload != nil {
		return payload
	}
	lines := strings.Split(text, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		if err := json.Unmarshal([]byte(line), &payload); err == nil && payload != nil {
			return payload
		}
	}
	return map[string]any{
		"job":        job,
		"raw_stdout": text,
	}
}

func exitCode(err *exec.ExitError) int {
	return err.ExitCode()
}

func FormatError(job string, err error, result map[string]any) error {
	if err == nil {
		return nil
	}
	if message, ok := result["message"].(string); ok && message != "" {
		return fmt.Errorf("%s failed: %s", job, message)
	}
	return fmt.Errorf("%s failed: %w", job, err)
}
