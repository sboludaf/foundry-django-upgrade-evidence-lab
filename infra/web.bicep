targetScope = 'resourceGroup'
param name string
param location string
param tags object
param accountName string
param projectEndpoint string
param bingConnectionId string
param operatorPrincipalId string
param modelCallLimit string
param bingQueryLimit string
param labExpiresAt string
@secure()
param djangoSecret string
var suffix = take(uniqueString(subscription().id, resourceGroup().id, name), 10)
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-django-${suffix}'
  location: location
  tags: tags
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stdjango${suffix}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Enabled'
  }
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {}
}
resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'evidence'
  properties: { publicAccess: 'None' }
}
resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {}
}
resource table 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'mvpstate'
  properties: {}
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acrdjango${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false, publicNetworkAccess: 'Enabled' }
}
resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: 'cae-django-${suffix}'
  location: location
  tags: tags
  properties: {
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = { name: accountName }
var roles = {
  blob: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  table: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  ai: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  pull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
}
resource blobRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in range(0, 2): {
  name: guid(storage.id, i == 0 ? identity.id : operatorPrincipalId, roles.blob)
  scope: storage
  properties: { principalId: i == 0 ? identity.properties.principalId : operatorPrincipalId, principalType: i == 0 ? 'ServicePrincipal' : 'User', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blob) }
}]
resource tableRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in range(0, 2): {
  name: guid(storage.id, i == 0 ? identity.id : operatorPrincipalId, roles.table)
  scope: storage
  properties: { principalId: i == 0 ? identity.properties.principalId : operatorPrincipalId, principalType: i == 0 ? 'ServicePrincipal' : 'User', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.table) }
}]
resource aiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, identity.id, roles.ai)
  scope: account
  properties: { principalId: identity.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.ai) }
}
resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, roles.pull)
  scope: registry
  properties: { principalId: identity.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.pull) }
}
var appName = 'web-django-${suffix}'
var fqdn = '${appName}.${environment.properties.defaultDomain}'
resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: appName
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
      secrets: [{ name: 'django-secret', value: djangoSecret }]
    }
    template: {
      containers: [{
        name: 'web'
        image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'DJANGO_SECRET_KEY', secretRef: 'django-secret' }
          { name: 'DJANGO_DEBUG', value: '0' }
          { name: 'DJANGO_ALLOWED_HOSTS', value: fqdn }
          { name: 'DJANGO_CSRF_TRUSTED_ORIGINS', value: 'https://${fqdn}' }
          { name: 'AZURE_CREDENTIAL_MODE', value: 'managed_identity' }
          { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
          { name: 'AZURE_AI_PROJECT_ENDPOINT', value: projectEndpoint }
          { name: 'AZURE_AI_ENDPOINT', value: 'https://${accountName}.services.ai.azure.com' }
          { name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME', value: 'django-luna' }
          { name: 'AZURE_DECISION_DEPLOYMENT', value: 'django-decision' }
          { name: 'BING_CONNECTION_ID', value: bingConnectionId }
          { name: 'BING_CUSTOM_SEARCH_INSTANCE_NAME', value: 'django-official' }
          { name: 'MODEL_CALL_LIMIT', value: modelCallLimit }
          { name: 'BING_QUERY_LIMIT', value: bingQueryLimit }
          { name: 'LAB_EXPIRES_AT', value: labExpiresAt }
        ]
      }]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
  dependsOn: [blobRoles, tableRoles, aiRole, pullRole]
}
output registryName string = registry.name
output registryEndpoint string = registry.properties.loginServer
output appName string = app.name
output appUrl string = 'https://${fqdn}'
output storageName string = storage.name
output identityClientId string = identity.properties.clientId
output environmentName string = environment.name
output environmentId string = environment.id
