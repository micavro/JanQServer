using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using Login;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Regist;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace JanqProbe;

internal sealed class AccountLoginContext
{
    public StoredAccount? Account { get; set; }

    public AccountData? AccountData { get; set; }

    public string? StableMenuKey { get; set; }

    public float StableMenuSince { get; set; }
}

internal static class AccountLoginBridge
{
    public const string Kind = "login_account";
    private const float RequiredStableMenuSeconds = 10f;

    public static bool Process(
        ActiveBridgeCommand item,
        out bool success,
        out string? error
    )
    {
        success = false;
        error = null;

        var scene = SceneManager.GetActiveScene().name;
        if (scene != "Login" && scene != "Regist")
        {
            return ProcessPostLoginScene(item, scene, out success, out error);
        }
        if (scene == "Regist")
        {
            error = "login_account_entered_registration";
            return true;
        }

        var manager = FindManager("LoginSequenceManager");
        var sequence = CurrentInnerSequence(manager);
        var sequenceName = sequence?.GetType().FullName;
        if (manager == null || sequence == null)
        {
            WriteStage("waiting_login_sequence", item, scene, sequenceName);
            return false;
        }

        if (TryDismissDialog(sequence, sequenceName))
        {
            WriteStage("dismissed_login_dialog", item, scene, sequenceName);
            return false;
        }

        if (sequenceName == "Login.SegaLogoSequence")
        {
            AccessTools.Method(sequence.GetType(), "onTouch")?.Invoke(sequence, new object?[] { null, null });
            WriteStage("advancing_login_logo", item, scene, sequenceName);
            return false;
        }

        if (sequenceName == "Login.LoginErrorSequence" || sequenceName?.Contains("LoginError") == true)
        {
            error = "login_account_error_sequence";
            return true;
        }

        try
        {
            if (item.Stage == 0)
            {
                var account = ResolveAccount(item.Command);
                var accountData = BuildAccountData(account);
                new AccountFileWriter().Save(accountData);
                item.Context = new AccountLoginContext
                {
                    Account = account,
                    AccountData = accountData
                };
                item.Stage = 1;
                ProbeLog.Write(
                    "login_account_prepared",
                    RedactedPayload(account, scene, sequenceName)
                );
                return false;
            }

            if (item.Stage == 1)
            {
                var context = Context(item) ?? throw new InvalidOperationException("login_account_context_missing");
                if (context.Account == null || context.AccountData == null)
                {
                    throw new InvalidOperationException("login_account_context_incomplete");
                }
                var request = CreateLoginRequest(context.Account, context.AccountData, manager);
                if (ForceNext(manager, request))
                {
                    item.Stage = 2;
                    ProbeLog.Write(
                        "login_account_request_sent",
                        RedactedPayload(context.Account, scene, sequenceName)
                    );
                }
                else
                {
                    WriteStage("waiting_login_force_next", item, scene, sequenceName);
                }
                return false;
            }

            WriteStage("waiting_login_account_completion", item, scene, sequenceName);
            return false;
        }
        catch (Exception ex)
        {
            error = ex.Message;
            return true;
        }
    }

    private static bool ProcessPostLoginScene(
        ActiveBridgeCommand item,
        string scene,
        out bool success,
        out string? error
    )
    {
        success = false;
        error = null;
        var context = Context(item);
        if (context == null)
        {
            context = new AccountLoginContext();
            item.Context = context;
        }

        if (scene != "Menu")
        {
            if (scene == "JanqGame")
            {
                return ProcessPostLoginJanqRecovery(item, context, scene, out success, out error);
            }

            ResetStableScene(context);
            WriteStage("waiting_post_login_menu_scene", item, scene, null);
            return false;
        }

        var menuManager = FindManager("MenuSequenceManager");
        var sequence = CurrentInnerSequence(menuManager);
        var sequenceName = sequence?.GetType().FullName;
        if (menuManager == null || sequenceName != "Menu.MainMenu")
        {
            ResetStableScene(context);
            WriteStage("waiting_post_login_main_menu", item, scene, sequenceName);
            return false;
        }

        var key = scene + "|" + sequenceName;
        if (context.StableMenuKey != key)
        {
            context.StableMenuKey = key;
            context.StableMenuSince = Time.realtimeSinceStartup;
            WriteStage("waiting_post_login_menu_stable", item, scene, sequenceName);
            return false;
        }

        var elapsed = Time.realtimeSinceStartup - context.StableMenuSince;
        if (elapsed < RequiredStableMenuSeconds)
        {
            WriteStage("waiting_post_login_menu_stable", item, scene, sequenceName);
            return false;
        }

        success = true;
        ProbeLog.Write(
            "login_account_completed",
            RedactedPayload(context.Account, scene, sequenceName)
        );
        return true;
    }

    private static bool ProcessPostLoginJanqRecovery(
        ActiveBridgeCommand item,
        AccountLoginContext context,
        string scene,
        out bool success,
        out string? error
    )
    {
        success = false;
        error = null;

        var manager = UnityEngine.Object.FindObjectOfType<Janq.GameManager>();
        if (manager == null)
        {
            ResetStableScene(context);
            WriteStage("waiting_post_login_janq_manager", item, scene, null);
            return false;
        }

        var state = CurrentGameState(manager);
        var requestState = CurrentGameRequestState(manager);
        if (string.IsNullOrWhiteSpace(state) || state == "None")
        {
            ResetStableScene(context);
            WriteStage("waiting_post_login_janq_ready", item, scene, $"{state}/{requestState}");
            return false;
        }

        var key = scene + "|JanqRecovery|" + state + "|" + requestState;
        if (context.StableMenuKey != key)
        {
            context.StableMenuKey = key;
            context.StableMenuSince = Time.realtimeSinceStartup;
            WriteStage("waiting_post_login_janq_stable", item, scene, $"{state}/{requestState}");
            return false;
        }

        var elapsed = Time.realtimeSinceStartup - context.StableMenuSince;
        if (elapsed < RequiredStableMenuSeconds)
        {
            WriteStage("waiting_post_login_janq_stable", item, scene, $"{state}/{requestState}");
            return false;
        }

        success = true;
        ProbeLog.Write(
            "login_account_completed",
            RedactedPayload(context.Account, scene, "JanqGame.recovery." + state)
        );
        return true;
    }

    private static void ResetStableScene(AccountLoginContext context)
    {
        context.StableMenuKey = null;
        context.StableMenuSince = 0f;
    }

    private static AccountLoginContext? Context(ActiveBridgeCommand item)
    {
        return item.Context as AccountLoginContext;
    }

    private static StoredAccount ResolveAccount(BridgeCommand command)
    {
        var selector = FirstNonEmpty(command.accountRequestId, command.accountSelector, command.account);
        if (string.IsNullOrWhiteSpace(selector))
        {
            throw new InvalidOperationException("login_account_selector_missing");
        }

        var path = ResolveAccountStorePath(command.accountStorePath);
        var accounts = LoadAccounts(path);
        var matches = accounts
            .Where(account => Matches(account, selector!))
            .ToList();
        if (matches.Count == 0)
        {
            throw new InvalidOperationException("login_account_not_found");
        }
        if (matches.Count > 1)
        {
            throw new InvalidOperationException("login_account_ambiguous");
        }

        var selected = matches[0];
        if (string.IsNullOrWhiteSpace(selected.loginId) || string.IsNullOrWhiteSpace(selected.password))
        {
            throw new InvalidOperationException("login_account_credentials_missing");
        }
        return selected;
    }

    private static string ResolveAccountStorePath(string? accountStorePath)
    {
        if (!string.IsNullOrWhiteSpace(accountStorePath))
        {
            return Path.GetFullPath(accountStorePath!);
        }
        return Path.GetFullPath(Path.Combine(ActionBridge.RootPath, "..", "accounts", "accounts.json"));
    }

    private static List<StoredAccount> LoadAccounts(string path)
    {
        if (!File.Exists(path))
        {
            throw new FileNotFoundException("account_store_missing", path);
        }
        var token = JToken.Parse(File.ReadAllText(path));
        if (token.Type == JTokenType.Array)
        {
            return token.ToObject<List<StoredAccount>>() ?? new List<StoredAccount>();
        }
        return token.ToObject<AccountStore>()?.accounts ?? new List<StoredAccount>();
    }

    private static bool Matches(StoredAccount account, string selector)
    {
        return EqualsIgnoreCase(account.requestId, selector)
            || EqualsIgnoreCase(account.loginId, selector)
            || EqualsIgnoreCase(account.nickname, selector);
    }

    private static bool EqualsIgnoreCase(string? left, string right)
    {
        return left != null && string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
    }

    private static AccountData BuildAccountData(StoredAccount account)
    {
        var data = LoadAccountData() as AccountData ?? new AccountData();
        data.ManualRegistData = new AccountData.RegistData
        {
            LoginID = account.loginId!.Trim(),
            Password = account.password!,
            Type = AccountData.AccountType.MJM_ID,
            IsLastCertifyOK = true,
            LoginKey = "",
            ContactID = ""
        };
        data.RememberMe = true;
        return data;
    }

    private static object CreateLoginRequest(StoredAccount account, AccountData accountData, object manager)
    {
        var loginData = new LoginData
        {
            ID = account.loginId!.Trim(),
            Password = account.password!,
            AccountType = AccountData.AccountType.MJM_ID,
            m_LoginKey = "",
            IsForceLogin = false,
            ContactID = ""
        };
        return CreateSequence("Login.RequestLoginSequence", loginData, accountData, manager);
    }

    private static bool TryDismissDialog(object sequence, string? sequenceName)
    {
        if (string.IsNullOrWhiteSpace(sequenceName))
        {
            return false;
        }
        if (sequenceName!.Contains("SafetyLogoutDialogSequence") || sequenceName.Contains("MessageDialogSequence"))
        {
            SetField(sequence, "isFinish", true);
            return true;
        }
        return false;
    }

    private static string? CurrentGameState(Janq.GameManager manager)
    {
        var mode = FieldText(manager, "mGameMode");
        return mode switch
        {
            "YakumanBonus" => FieldText(manager, "mGameStateBonus"),
            "ParenChallenge" => FieldText(manager, "mGameStateChallenge"),
            _ => FieldText(manager, "mGameStateNormal")
        };
    }

    private static string? CurrentGameRequestState(Janq.GameManager manager)
    {
        var mode = FieldText(manager, "mGameMode");
        return mode switch
        {
            "YakumanBonus" => FieldText(manager, "mGameStateBonusRequest"),
            "ParenChallenge" => FieldText(manager, "mGameStateChallengeRequest"),
            _ => FieldText(manager, "mGameStateNormalRequest")
        };
    }

    private static object? FieldValue(object instance, string name)
    {
        return FieldInHierarchy(instance.GetType(), name)?.GetValue(instance);
    }

    private static string? FieldText(object instance, string name)
    {
        return FieldValue(instance, name)?.ToString();
    }

    private static object? LoadAccountData()
    {
        var reader = Activator.CreateInstance(RequiredType("Regist.AccountFileReader"))
            ?? throw new InvalidOperationException("account_file_reader_create_failed");
        Invoke(reader, "Load");
        return AccessTools.Property(reader.GetType(), "Data")?.GetValue(reader);
    }

    private static object? FindManager(string fullName)
    {
        return UnityEngine.Object.FindObjectsOfType<MonoBehaviour>()
            .FirstOrDefault(item => item.GetType().FullName == fullName);
    }

    private static object? CurrentInnerSequence(object? manager)
    {
        if (manager == null)
        {
            return null;
        }
        object? sequence = AccessTools.Field(manager.GetType(), "_currentSequence")?.GetValue(manager);
        while (sequence != null)
        {
            var inner = FieldInHierarchy(sequence.GetType(), "sequence")?.GetValue(sequence);
            if (inner == null || ReferenceEquals(inner, sequence))
            {
                return sequence;
            }
            sequence = inner;
        }
        return null;
    }

    private static object CreateSequence(string typeName, params object[] args)
    {
        var type = RequiredType(typeName);
        var method = type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Where(candidate => candidate.Name == "Create")
            .FirstOrDefault(candidate => ParametersMatch(candidate.GetParameters(), args))
            ?? throw new MissingMethodException(typeName, "Create");
        return method.Invoke(null, args)
            ?? throw new InvalidOperationException(typeName + ".Create returned null");
    }

    private static bool ForceNext(object manager, object next)
    {
        var method = manager.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            .FirstOrDefault(candidate => candidate.Name == "RequestForceNextSequence" && candidate.GetParameters().Length == 1)
            ?? throw new MissingMethodException(manager.GetType().FullName, "RequestForceNextSequence");
        return method.Invoke(manager, new[] { next }) as bool? ?? false;
    }

    private static object? Invoke(object instance, string methodName, params object[] args)
    {
        var method = instance.GetType().GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .Where(candidate => candidate.Name == methodName)
            .FirstOrDefault(candidate => ParametersMatch(candidate.GetParameters(), args))
            ?? throw new MissingMethodException(instance.GetType().FullName, methodName);
        return method.Invoke(instance, args);
    }

    private static bool ParametersMatch(ParameterInfo[] parameters, object[] args)
    {
        if (parameters.Length != args.Length)
        {
            return false;
        }
        for (var index = 0; index < parameters.Length; index++)
        {
            if (args[index] != null && !parameters[index].ParameterType.IsInstanceOfType(args[index]))
            {
                return false;
            }
        }
        return true;
    }

    private static void SetField(object instance, string name, object? value)
    {
        var field = FieldInHierarchy(instance.GetType(), name)
            ?? throw new MissingFieldException(instance.GetType().FullName, name);
        field.SetValue(instance, value);
    }

    private static FieldInfo? FieldInHierarchy(Type type, string name)
    {
        for (var current = type; current != null; current = current.BaseType)
        {
            var field = current.GetField(name, BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
            if (field != null)
            {
                return field;
            }
        }
        return null;
    }

    private static Type RequiredType(string name)
    {
        return AccessTools.TypeByName(name) ?? throw new TypeLoadException(name);
    }

    private static string? FirstNonEmpty(params string?[] values)
    {
        foreach (var value in values)
        {
            if (!string.IsNullOrWhiteSpace(value))
            {
                return value!.Trim();
            }
        }
        return null;
    }

    private static string MaskLoginId(string? loginId)
    {
        if (string.IsNullOrWhiteSpace(loginId))
        {
            return "";
        }
        var text = loginId!.Trim();
        return text.Length <= 3 ? "***" : text.Substring(0, 3) + "***";
    }

    private static object RedactedPayload(StoredAccount? account, string? scene, string? sequence)
    {
        return new
        {
            requestId = account?.requestId,
            loginId = MaskLoginId(account?.loginId),
            nickname = account?.nickname,
            finalMjchip = account?.finalMjchip,
            status = account?.status,
            scene,
            sequence
        };
    }

    private static void WriteStage(string stage, ActiveBridgeCommand item, string? scene, string? sequence)
    {
        var statusKey = string.Join("|", stage, item.Stage.ToString(), scene ?? "", sequence ?? "");
        if (item.LastStatusKey == statusKey)
        {
            return;
        }
        item.LastStatusKey = statusKey;
        ProbeLog.Write(
            "login_account_stage",
            new
            {
                stage,
                item.Stage,
                scene,
                sequence,
                account = RedactedPayload(Context(item)?.Account, null, null)
            }
        );
    }
}
