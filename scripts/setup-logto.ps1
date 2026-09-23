[CmdletBinding()]
param(
    [string]$LogtoEndpoint,
    [string]$ManagementEndpoint,
    [string]$ManagementApiResource,
    [string]$ApiResourceIndicator = 'https://api.lxscope.local',
    [string]$WebOrigin,
    [string]$BackendLogtoEndpoint,
    [string]$BackendJwksUri,
    [string]$OrganizationName = 'lxScope',
    [string]$AdminEmail,
    [switch]$ConfigureOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $ProjectRoot '.env'
$EnvExamplePath = Join-Path $ProjectRoot '.env.example'
$ApiResourceName = 'lxScope API'
$SpaApplicationName = 'lxScope Web'
$MemberRoleName = 'lxscope_member'
$AdminRoleName = 'lxscope_admin'

function Read-RequiredValue {
    param([string]$Prompt, [string]$Default = '')
    while ($true) {
        if ($Default) {
            $answer = Read-Host "$Prompt [$Default]"
            if ([string]::IsNullOrWhiteSpace($answer)) { $answer = $Default }
        }
        else {
            $answer = Read-Host $Prompt
        }
        if (-not [string]::IsNullOrWhiteSpace($answer)) { return $answer.Trim() }
        Write-Host '此项必填。' -ForegroundColor Yellow
    }
}

function Read-SecureValue {
    param([string]$Prompt)
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Invoke-LogtoApi {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('GET', 'POST', 'PATCH', 'PUT')][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [object]$Body
    )

    $request = @{
        Method     = $Method
        Uri        = "$script:ApiBase$Path"
        Headers    = $script:ApiHeaders
        ErrorAction = 'Stop'
    }
    if ($PSBoundParameters.ContainsKey('Body')) {
        $request.ContentType = 'application/json'
        $request.Body = ConvertTo-Json -InputObject $Body -Depth 30 -Compress
    }

    try {
        return Invoke-RestMethod @request
    }
    catch {
        $message = $_.Exception.Message
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $message = $_.ErrorDetails.Message
        }
        throw "Logto API $Method $Path 失败：$message。请确认 M2M 应用已分配 Logto Management API access 权限。"
    }
}

function Get-LogtoPages {
    param([Parameter(Mandatory = $true)][string]$Path)

    $items = @()
    for ($page = 1; $page -le 100; $page++) {
        $separator = if ($Path.Contains('?')) { '&' } else { '?' }
        $response = Invoke-LogtoApi -Method GET -Path "$Path$separator`page=$page&page_size=100"
        $pageItems = @($response)
        if ($pageItems.Count -eq 0) { break }
        $items += $pageItems
        if ($pageItems.Count -lt 100) { break }
    }
    return ,$items
}

function Merge-UniqueStrings {
    param([object[]]$Existing, [object[]]$Additional)
    $result = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($value in @($Existing) + @($Additional)) {
        if ($null -eq $value) { continue }
        $text = [string]$value
        if (-not [string]::IsNullOrWhiteSpace($text) -and $seen.Add($text)) {
            $result.Add($text)
        }
    }
    return ,$result.ToArray()
}

function Get-ObjectPropertyMap {
    param([object]$Object)
    $map = @{}
    if ($null -ne $Object) {
        foreach ($property in $Object.PSObject.Properties) {
            $map[$property.Name] = $property.Value
        }
    }
    return $map
}

function Set-EnvValue {
    param([string]$Name, [AllowEmptyString()][string]$Value)
    $found = $false
    for ($index = 0; $index -lt $script:EnvLines.Count; $index++) {
        if ($script:EnvLines[$index] -match "^\s*$([regex]::Escape($Name))=") {
            $script:EnvLines[$index] = "$Name=$Value"
            $found = $true
        }
    }
    if (-not $found) { $script:EnvLines.Add("$Name=$Value") }
}

function Get-EnvValue {
    param([string]$Name)
    foreach ($line in $script:EnvLines) {
        if ($line -match "^\s*$([regex]::Escape($Name))=(.*)$") { return $Matches[1].Trim() }
    }
    return ''
}

function Read-OptionalValue {
    param([string]$Prompt, [string]$Default = '')
    if ($Default) { $answer = Read-Host "$Prompt [$Default]" }
    else { $answer = Read-Host $Prompt }
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}

function Ensure-LogtoScope {
    param([string]$ResourceId, [object[]]$ExistingScopes, [string]$Name, [string]$Description)
    $scope = @($ExistingScopes | Where-Object { $_.name -eq $Name } | Select-Object -First 1)
    if ($scope.Count -gt 0) { return $scope[0] }
    Write-Host "创建 API 权限：$Name"
    return Invoke-LogtoApi -Method POST -Path "/resources/$ResourceId/scopes" -Body @{
        name = $Name
        description = $Description
    }
}

function Ensure-OrganizationRole {
    param([object[]]$AllRoles, [string]$Name, [string]$Description, [string[]]$ScopeIds)
    $matching = @($AllRoles | Where-Object { $_.name -eq $Name })
    if ($matching.Count -gt 1) { throw "Logto 中存在多个名为 $Name 的组织角色；请先整理重复项。" }
    if ($matching.Count -eq 0) {
        Write-Host "创建组织角色：$Name"
        return Invoke-LogtoApi -Method POST -Path '/organization-roles' -Body @{
            name = $Name
            description = $Description
            type = 'User'
            organizationScopeIds = @()
            resourceScopeIds = @($ScopeIds)
        }
    }

    $role = $matching[0]
    if ($role.type -ne 'User') { throw "组织角色 $Name 不是 User 类型，已停止以免改动其用途。" }
    $encodedId = [Uri]::EscapeDataString([string]$role.id)
    $assigned = @(Get-LogtoPages -Path "/organization-roles/$encodedId/resource-scopes")
    $assignedIds = @($assigned | ForEach-Object { [string]$_.id })
    if ($Name -eq $MemberRoleName -and @($assigned | Where-Object { $_.name -eq 'tenant:manage' }).Count -gt 0) {
        throw "现有 $MemberRoleName 已包含 tenant:manage。请先检查该角色，避免普通成员获得管理员权限。"
    }
    $missingIds = @($ScopeIds | Where-Object { $_ -notin $assignedIds })
    if ($missingIds.Count -gt 0) {
        Write-Host "补齐组织角色权限：$Name"
        Invoke-LogtoApi -Method POST -Path "/organization-roles/$encodedId/resource-scopes" -Body @{ scopeIds = $missingIds } | Out-Null
    }
    return $role
}

try {
    if (-not (Test-Path -LiteralPath $EnvExamplePath)) { throw '找不到仓库根目录的 .env.example。请从 scripts 目录运行此脚本。' }
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        Copy-Item -LiteralPath $EnvExamplePath -Destination $EnvPath
        Write-Host '已从 .env.example 创建 .env。'
    }

    $script:EnvLines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in [System.IO.File]::ReadAllLines($EnvPath)) { $script:EnvLines.Add($line) }

    if (-not $LogtoEndpoint) { $LogtoEndpoint = Read-RequiredValue 'Logto 地址（基础 URL，不要附加 /oidc）' }
    $LogtoEndpoint = $LogtoEndpoint.Trim().TrimEnd('/') -ireplace '/oidc$', ''
    $endpointUri = $null
    if (-not [Uri]::TryCreate($LogtoEndpoint, [UriKind]::Absolute, [ref]$endpointUri) -or $endpointUri.Scheme -notin @('http', 'https')) {
        throw 'Logto 地址必须是有效的 http:// 或 https:// URL。'
    }
    if (-not $ManagementEndpoint) {
        $ManagementEndpoint = Read-OptionalValue '管理 API 使用的 Logto 地址（Cloud 自定义域名请填租户默认 *.logto.app；其他情况直接回车）' $LogtoEndpoint
    }
    $ManagementEndpoint = $ManagementEndpoint.Trim().TrimEnd('/') -ireplace '/oidc$', ''
    $managementUri = $null
    if (-not [Uri]::TryCreate($ManagementEndpoint, [UriKind]::Absolute, [ref]$managementUri) -or $managementUri.Scheme -notin @('http', 'https')) {
        throw 'Management API 地址必须是有效的 http:// 或 https:// URL。'
    }
    if (-not $ManagementApiResource) {
        $ManagementApiResource = Read-RequiredValue 'Logto Management API Resource Indicator' "$ManagementEndpoint/api"
    }
    if (-not $ApiResourceIndicator) {
        $ApiResourceIndicator = Read-RequiredValue 'lxScope API Resource Indicator' 'https://api.lxscope.local'
    }
    if (-not $WebOrigin) { $WebOrigin = Read-RequiredValue '浏览器访问 lxScope 的地址（只填 origin）' 'http://localhost:8000' }
    $WebOrigin = $WebOrigin.Trim().TrimEnd('/')
    $webUri = $null
    if (-not [Uri]::TryCreate($WebOrigin, [UriKind]::Absolute, [ref]$webUri) -or $webUri.Scheme -notin @('http', 'https') -or $webUri.AbsolutePath -ne '/' -or $webUri.Query -or $webUri.Fragment) {
        throw '浏览器地址必须是 origin，例如 http://localhost:8000，不要附加路径。'
    }
    if (-not $AdminEmail) { $AdminEmail = Read-RequiredValue '用于初始管理员的现有 Logto 用户邮箱' }
    if ($AdminEmail -notmatch '^\S+@\S+\.\S+$') { throw '管理员邮箱格式无效。' }

    $m2mClientId = Read-RequiredValue 'Logto Management M2M App ID'
    $m2mClientSecret = Read-SecureValue 'Logto Management M2M App Secret'
    if ([string]::IsNullOrWhiteSpace($m2mClientSecret)) { throw 'M2M App Secret 不能为空。' }

    $tokenEndpoint = "$ManagementEndpoint/oidc/token"
    $basicBytes = [Text.Encoding]::UTF8.GetBytes("${m2mClientId}:$m2mClientSecret")
    $basic = [Convert]::ToBase64String($basicBytes)
    Write-Host '正在连接 Logto Management API…'
    try {
        $tokenResponse = Invoke-RestMethod -Method POST -Uri $tokenEndpoint `
            -Headers @{ Authorization = "Basic $basic" } `
            -ContentType 'application/x-www-form-urlencoded' `
            -Body @{ grant_type = 'client_credentials'; resource = $ManagementApiResource; scope = 'all' } `
            -ErrorAction Stop
    }
    catch {
        throw "无法取得 Logto 管理令牌。请检查 Logto 地址、Management API Resource Indicator，以及 M2M 应用是否已获 Logto Management API access 权限。详情：$($_.Exception.Message)"
    }
    if (-not $tokenResponse.access_token) { throw 'Logto 没有返回管理访问令牌。' }
    $script:ApiBase = "$ManagementEndpoint/api"
    $script:ApiHeaders = @{ Authorization = "Bearer $($tokenResponse.access_token)" }

    # Fail before provisioning if the requested bootstrap account does not exist.
    $users = @(Get-LogtoPages -Path '/users')
    $matchingUsers = @($users | Where-Object { $_.primaryEmail -and $_.primaryEmail.Equals($AdminEmail, [StringComparison]::OrdinalIgnoreCase) })
    if ($matchingUsers.Count -eq 0) { throw "Logto 中找不到 $AdminEmail。请先在 Logto 创建该用户，再重新运行脚本。" }
    if ($matchingUsers.Count -gt 1) { throw "Logto 中有多个邮箱为 $AdminEmail 的用户；无法安全选择初始管理员。" }
    $adminUser = $matchingUsers[0]

    $redirectUri = "$WebOrigin/auth/callback"
    Write-Host '检查 API Resource 和权限…'
    $resources = @(Get-LogtoPages -Path '/resources')
    $matchingResources = @($resources | Where-Object { $_.indicator -eq $ApiResourceIndicator })
    if ($matchingResources.Count -gt 1) { throw "Logto 中存在多个 Indicator 为 $ApiResourceIndicator 的 API Resource。" }
    if ($matchingResources.Count -eq 0) {
        $resource = Invoke-LogtoApi -Method POST -Path '/resources' -Body @{
            name = $ApiResourceName
            indicator = $ApiResourceIndicator
            accessTokenTtl = 3600
        }
    }
    else { $resource = $matchingResources[0] }

    $resourceId = [Uri]::EscapeDataString([string]$resource.id)
    $resourceScopes = @(Get-LogtoPages -Path "/resources/$resourceId/scopes")
    $accessScope = Ensure-LogtoScope -ResourceId $resourceId -ExistingScopes $resourceScopes -Name 'agent:use' -Description 'Allow organization members to use lxScope.'
    $resourceScopes += $accessScope
    $adminScope = Ensure-LogtoScope -ResourceId $resourceId -ExistingScopes $resourceScopes -Name 'tenant:manage' -Description 'Allow organization administrators to manage tenant settings.'

    Write-Host '检查 Organization 角色…'
    $roles = @(Get-LogtoPages -Path '/organization-roles')
    $memberRole = Ensure-OrganizationRole -AllRoles $roles -Name $MemberRoleName -Description 'lxScope organization member.' -ScopeIds @([string]$accessScope.id)
    $roles += $memberRole
    $adminRole = Ensure-OrganizationRole -AllRoles $roles -Name $AdminRoleName -Description 'lxScope organization administrator.' -ScopeIds @([string]$accessScope.id, [string]$adminScope.id)

    Write-Host '检查 SPA 应用和回调地址…'
    $applications = @(Get-LogtoPages -Path '/applications')
    $matchingApps = @($applications | Where-Object { $_.name -eq $SpaApplicationName -and $_.type -eq 'SPA' })
    if ($matchingApps.Count -gt 1) { throw "Logto 中存在多个名为 $SpaApplicationName 的 SPA 应用。" }
    if ($matchingApps.Count -eq 0) {
        $app = Invoke-LogtoApi -Method POST -Path '/applications' -Body @{
            name = $SpaApplicationName
            description = 'lxScope web client managed by setup-logto.ps1.'
            type = 'SPA'
            oidcClientMetadata = @{
                redirectUris = @($redirectUri)
                postLogoutRedirectUris = @($WebOrigin)
            }
            customClientMetadata = @{ corsAllowedOrigins = @($WebOrigin) }
        }
    }
    else {
        $app = $matchingApps[0]
        $metadata = Get-ObjectPropertyMap $app.oidcClientMetadata
        $metadata.redirectUris = Merge-UniqueStrings -Existing @($metadata.redirectUris) -Additional @($redirectUri)
        $metadata.postLogoutRedirectUris = Merge-UniqueStrings -Existing @($metadata.postLogoutRedirectUris) -Additional @($WebOrigin)
        $customMetadata = Get-ObjectPropertyMap $app.customClientMetadata
        $customMetadata.corsAllowedOrigins = Merge-UniqueStrings -Existing @($customMetadata.corsAllowedOrigins) -Additional @($WebOrigin)
        $appIdPath = [Uri]::EscapeDataString([string]$app.id)
        $app = Invoke-LogtoApi -Method PATCH -Path "/applications/$appIdPath" -Body @{
            oidcClientMetadata = $metadata
            customClientMetadata = $customMetadata
        }
    }
    $appId = [string]$app.id
    if ([string]::IsNullOrWhiteSpace($appId)) { throw 'Logto SPA 应用未返回 App ID。' }

    # Preserve existing consents and add the scopes requested by the current frontend.
    $consents = Invoke-LogtoApi -Method GET -Path "/applications/$([Uri]::EscapeDataString($appId))/user-consent-scopes"
    $existingOrgResourceIds = @(
        foreach ($group in @($consents.organizationResourceScopes)) {
            foreach ($scope in @($group.scopes)) { [string]$scope.id }
        }
    )
    $existingResourceIds = @(
        foreach ($group in @($consents.resourceScopes)) {
            foreach ($scope in @($group.scopes)) { [string]$scope.id }
        }
    )
    $existingOrganizationIds = @($consents.organizationScopes | ForEach-Object { [string]$_.id })
    $existingUserScopes = @($consents.userScopes)
    $consentBody = @{
        organizationScopes = Merge-UniqueStrings -Existing $existingOrganizationIds -Additional @()
        resourceScopes = Merge-UniqueStrings -Existing $existingResourceIds -Additional @()
        organizationResourceScopes = Merge-UniqueStrings -Existing $existingOrgResourceIds -Additional @([string]$accessScope.id, [string]$adminScope.id)
        userScopes = Merge-UniqueStrings -Existing $existingUserScopes -Additional @('urn:logto:scope:organizations')
    }
    Invoke-LogtoApi -Method POST -Path "/applications/$([Uri]::EscapeDataString($appId))/user-consent-scopes" -Body $consentBody | Out-Null

    Write-Host '检查 Organization，并为初始管理员分配角色…'
    $organizations = @(Get-LogtoPages -Path '/organizations')
    $matchingOrganizations = @($organizations | Where-Object { $_.name -eq $OrganizationName })
    if ($matchingOrganizations.Count -gt 1) { throw "Logto 中存在多个名为 $OrganizationName 的 Organization。" }
    if ($matchingOrganizations.Count -eq 0) {
        $organization = Invoke-LogtoApi -Method POST -Path '/organizations' -Body @{
            name = $OrganizationName
            description = 'Organization provisioned for lxScope.'
        }
    }
    else { $organization = $matchingOrganizations[0] }
    $organizationId = [Uri]::EscapeDataString([string]$organization.id)
    $adminUserId = [Uri]::EscapeDataString([string]$adminUser.id)
    Invoke-LogtoApi -Method POST -Path "/organizations/$organizationId/users" -Body @{ userIds = @([string]$adminUser.id) } | Out-Null
    Invoke-LogtoApi -Method POST -Path "/organizations/$organizationId/users/$adminUserId/roles" -Body @{ organizationRoleIds = @([string]$adminRole.id) } | Out-Null

    # New members provisioned through Logto JIT join as ordinary lxScope members.
    $jitRoles = @(Get-LogtoPages -Path "/organizations/$organizationId/jit/roles")
    if (@($jitRoles | Where-Object { $_.id -eq $memberRole.id }).Count -eq 0) {
        Invoke-LogtoApi -Method POST -Path "/organizations/$organizationId/jit/roles" -Body @{ organizationRoleIds = @([string]$memberRole.id) } | Out-Null
    }

    Set-EnvValue -Name 'LXSCOPE_AUTH_PROVIDER' -Value 'logto'
    Set-EnvValue -Name 'LOGTO_ENDPOINT' -Value $LogtoEndpoint
    Set-EnvValue -Name 'LOGTO_API_RESOURCE' -Value $ApiResourceIndicator
    Set-EnvValue -Name 'LOGTO_ACCESS_SCOPE' -Value 'agent:use'
    Set-EnvValue -Name 'LOGTO_ADMIN_SCOPE' -Value 'tenant:manage'
    Set-EnvValue -Name 'VITE_LOGTO_ENDPOINT' -Value $LogtoEndpoint
    Set-EnvValue -Name 'VITE_LOGTO_APP_ID' -Value $appId
    Set-EnvValue -Name 'VITE_LOGTO_API_RESOURCE' -Value $ApiResourceIndicator

    if (-not $BackendLogtoEndpoint) {
        $publicUri = [Uri]$LogtoEndpoint
        if ($publicUri.Host -in @('localhost', '127.0.0.1')) {
            $dockerDesktopDefault = $LogtoEndpoint -replace '://(localhost|127\.0\.0\.1)', '://host.docker.internal'
            $BackendLogtoEndpoint = Read-OptionalValue '容器内可访问的 Logto 地址（本机 Logto/Docker Desktop 建议使用默认值；其他情况留空保留现有设置）' $dockerDesktopDefault
        }
    }
    if (-not $BackendLogtoEndpoint) { $BackendLogtoEndpoint = Get-EnvValue 'LOGTO_INTERNAL_ENDPOINT' }
    if ($BackendLogtoEndpoint) {
        $BackendLogtoEndpoint = $BackendLogtoEndpoint.Trim().TrimEnd('/') -ireplace '/oidc$', ''
        $backendUri = $null
        if (-not [Uri]::TryCreate($BackendLogtoEndpoint, [UriKind]::Absolute, [ref]$backendUri) -or $backendUri.Scheme -notin @('http', 'https')) {
            throw '容器内 Logto 地址必须是有效的 http:// 或 https:// URL。'
        }
        if (-not $BackendJwksUri) { $BackendJwksUri = Get-EnvValue 'LOGTO_JWKS_URI' }
        if (-not $BackendJwksUri) { $BackendJwksUri = "$BackendLogtoEndpoint/oidc/jwks" }
        Set-EnvValue -Name 'LOGTO_INTERNAL_ENDPOINT' -Value $BackendLogtoEndpoint
        Set-EnvValue -Name 'LOGTO_JWKS_URI' -Value $BackendJwksUri
    }
    [System.IO.File]::WriteAllLines($EnvPath, $script:EnvLines.ToArray(), [System.Text.UTF8Encoding]::new($false))

    Write-Host ''
    Write-Host 'Logto 已配置完成。' -ForegroundColor Green
    Write-Host "组织：$OrganizationName"
    Write-Host "初始管理员：$AdminEmail"
    Write-Host "SPA App ID：$appId"
    Write-Host "登录回调：$redirectUri"
    Write-Host "管理 API 地址：$ManagementEndpoint"
    Write-Host "环境配置已写入：$EnvPath"
    Write-Host 'M2M Secret 仅用于本次初始化，没有写入 .env。'

    if (-not $ConfigureOnly) {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw '找不到 Docker 命令。请安装并启动 Docker Desktop，然后重新运行。' }
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Docker Engine 尚未启动。启动 Docker Desktop 后重新运行脚本。' }
        Push-Location $ProjectRoot
        try {
            & docker compose up -d --build
            if ($LASTEXITCODE -ne 0) { throw 'Docker Compose 启动失败；检查上方输出后可重新运行此脚本。' }
        }
        finally { Pop-Location }
        Write-Host "部署已启动：$WebOrigin" -ForegroundColor Green
        Start-Process $WebOrigin
    }
    else {
        Write-Host '配置已写入；之后在仓库根目录运行 docker compose up -d --build 即可启动。'
    }
}
catch {
    Write-Error $_
    exit 1
}
finally {
    $m2mClientSecret = $null
    $basic = $null
    $basicBytes = $null
    $tokenResponse = $null
    $script:ApiHeaders = $null
}
